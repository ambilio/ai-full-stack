"""ClaimAssist API — v5 (Session 10). ClaimAssist is complete.

Endpoints:
    GET  /health              -> liveness + active prompt_version
    GET  /claims/{claim_id}   -> the raw claim record (404 if unknown)
    POST /ask                 -> guarded, traced, grounded, structured answer

THE v4 -> v5 CHANGES (LLMOps + Responsible AI):
  1. PROMPT REGISTRY  — the system prompt is no longer a string in this file;
     it is loaded from prompts/registry.yaml via prompts/loader.py, and the
     active version is selected by env PROMPT_VERSION. Promoting a prompt is
     an .env change — a release, not a code edit.
  2. TRACING          — the openai client is the langfuse.openai DROP-IN
     wrapper, so every LLM generation (latency, tokens, prompt, completion)
     is traced; @observe wraps the /ask handler so the whole request is one
     trace, tagged with prompt_version and the caller's session id.
     If LANGFUSE_* keys are unset, everything degrades to a NO-OP: the app
     runs identically, just untraced.
  3. GUARDRAILS       — middleware order: input guard -> traced LLM call ->
     output guard (guardrails.py). Refusals return
     {answer: <refusal>, refused: true, reason: ...} — never a 500.

Note on streaming: v5's /ask is non-streaming BY DESIGN. The output guard
must see the complete answer before anything leaves the boundary — you cannot
un-stream a leaked phone number. Production systems that stream moderate in
buffered windows; that trade-off is discussed in the deck.
"""

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Dict, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from guardrails import REFUSAL, check_input, check_output
from prompts.loader import load_prompt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("claimassist.api")

# --- configuration (all via env; see docker-compose.yml / .env) -------------
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:4000/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "local")
LLM_MODEL = os.getenv("LLM_MODEL", "openrouter-mini")
API_KEY = os.getenv("API_KEY", "local-dev-key")
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1")
DATA_FILE = Path(os.getenv("DATA_FILE", "data/claims.json"))

# --- prompt registry: fail FAST at startup on an unknown version ------------
ACTIVE_PROMPT = load_prompt("answer_grounded", PROMPT_VERSION)

# --- Langfuse tracing: drop-in wrapper, graceful no-op without keys ----------
# With LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST set, the
# langfuse.openai import is a DROP-IN for the openai SDK: same classes, same
# calls, every generation traced. Without keys (or without the package) the
# app must behave identically — observability must never take the product down.
LANGFUSE_ENABLED = bool(
    os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
)
if LANGFUSE_ENABLED:
    try:
        from langfuse.decorators import langfuse_context, observe
        from langfuse.openai import OpenAI  # the drop-in wrapper
        logger.info("Langfuse tracing ENABLED (host=%s)",
                    os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))
    except Exception as exc:  # missing package, bad keys — degrade, don't die
        logger.warning("Langfuse unavailable (%s) — tracing disabled", exc)
        LANGFUSE_ENABLED = False
if not LANGFUSE_ENABLED:
    from openai import OpenAI  # plain client, no tracing

    langfuse_context = None

    def observe(*_args, **_kwargs):  # no-op decorator, same signature
        def decorator(fn):
            return fn
        return decorator


client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=60.0,
                max_retries=2)

app = FastAPI(title="ClaimAssist API", version="5.0.0")

# --- data --------------------------------------------------------------------
with open(DATA_FILE, encoding="utf-8") as f:
    CLAIMS: Dict[str, dict] = {c["claim_id"]: c for c in json.load(f)}


# --- auth (Session 2 discipline: shared key at the boundary) ------------------
def require_api_key(x_api_key: str = Header(default="")) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


# --- models -------------------------------------------------------------------
class AskRequest(BaseModel):
    question: str
    claim_id: Optional[str] = None


class AskResponse(BaseModel):
    """v5 contract: refusals are FIRST-CLASS fields, not HTTP errors."""

    answer: str
    claim_id: Optional[str] = None
    status: Optional[str] = None
    confidence: Literal["high", "medium", "low"]
    refused: bool = False
    reason: Optional[str] = None
    prompt_version: str = PROMPT_VERSION


# --- prompt construction --------------------------------------------------------
def build_messages(question: str, record: Optional[dict]) -> list:
    system = ACTIVE_PROMPT["text"]
    if record is not None:
        system += "\n\nClaim record (ground truth):\n" + json.dumps(record, indent=2)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]


# --- endpoints -------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "llm_base_url": LLM_BASE_URL,
        "model": LLM_MODEL,
        "prompt_version": PROMPT_VERSION,
        "langfuse_enabled": LANGFUSE_ENABLED,
    }


@app.get("/claims/{claim_id}")
def get_claim(claim_id: str):
    record = CLAIMS.get(claim_id.upper())
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown claim_id {claim_id}")
    return record


@app.post("/ask", response_model=AskResponse, dependencies=[Depends(require_api_key)])
@observe(name="claimassist-ask")  # one trace per /ask request
async def ask(
    req: AskRequest,
    x_session_id: Optional[str] = Header(default=None, alias="X-Session-Id"),
):
    """Middleware order: input guard -> traced LLM call -> output guard."""
    session_id = x_session_id or f"anon-{uuid.uuid4().hex[:8]}"

    # Tag the trace so it is filterable in Langfuse: prompt_version drives the
    # A/B comparison; session_id groups a conversation end-to-end.
    if LANGFUSE_ENABLED:
        langfuse_context.update_current_trace(
            session_id=session_id,
            tags=[f"prompt_version:{PROMPT_VERSION}", "app:claimassist"],
        )

    # ---- layer 1: input guard (before the model sees anything) --------------
    verdict = check_input(req.question)
    if not verdict["allowed"]:
        return AskResponse(
            answer=REFUSAL,
            confidence="high",          # a refusal is a confident decision
            refused=True,
            reason=verdict["reason"],
        )

    record = None
    if req.claim_id:
        record = CLAIMS.get(req.claim_id.upper())
        if record is None:
            raise HTTPException(status_code=404,
                                detail=f"Unknown claim_id {req.claim_id}")

    # ---- layer 2: the traced LLM call ---------------------------------------
    # The langfuse.openai wrapper records this generation (model, latency,
    # token usage, prompt, completion) inside the current trace automatically.
    t0 = time.perf_counter()
    try:
        completion = client.chat.completions.create(
            model=LLM_MODEL,
            messages=build_messages(req.question, record),
            max_tokens=220,
        )
        answer = (completion.choices[0].message.content or "").strip()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Upstream LLM error: {exc}")
    latency_ms = round((time.perf_counter() - t0) * 1000)
    logger.info("llm_call ok latency_ms=%s prompt_version=%s session=%s",
                latency_ms, PROMPT_VERSION, session_id)

    # ---- layer 3: output guard (before anything leaves the boundary) --------
    out = check_output(answer)

    if not out["text"]:
        return AskResponse(answer="I could not generate an answer. Please contact "
                                  "the claims helpline.", confidence="low")

    confidence = "high" if record is not None else "medium"
    return AskResponse(
        answer=out["text"],
        claim_id=record["claim_id"] if record else None,
        status=record["status"] if record else None,
        confidence="high" if out["refused"] else confidence,
        refused=out["refused"],
        reason=out["reason"],
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
