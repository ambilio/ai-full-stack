"""ClaimAssist API — v4 (Session 9).

Endpoints:
    GET  /health              -> liveness
    GET  /claims/{claim_id}   -> the raw claim record (404 if unknown)
    POST /ask                 -> grounded, structured (Pydantic) answer
    POST /ask/stream          -> SSE streaming answer
    POST /agent               -> NEW in v4: single-agent tool use over MCP
                                 {question, confirm?} -> {answer, tool_trace}

THE v3 -> v4 CHANGE: the /agent endpoint. It delegates to one of two runners:
    AGENT_MODE=llm (default) — real function calling; the MODEL chooses the tools
        (agent/loop.py). Uses openrouter-mini behind LiteLLM.
    AGENT_MODE=router — deterministic intent routing for the local
        0.5B model (agent/local_router.py); the MCP mechanics run end-to-end
        offline when LLM_MODEL=qwen-local.
Both runners execute tools against the SAME MCP server, spawned as a child
process of this API over stdio — no extra container, no extra port.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import AsyncIterator, Dict, List, Literal, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Make the agent package importable both in the container (/app/agent, next to
# this file) and when run from the project root on the host (api/../agent).
_HERE = Path(__file__).resolve().parent
for p in (_HERE, _HERE.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from agent.local_router import run_router  # noqa: E402
from agent.loop import run_agent  # noqa: E402

# --- configuration (all via env; see docker-compose.yml / .env) -------------
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:4000/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "local")
LLM_MODEL = os.getenv("LLM_MODEL", "openrouter-mini")
API_KEY = os.getenv("API_KEY", "local-dev-key")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "60"))
LLM_RETRIES = int(os.getenv("LLM_RETRIES", "3"))
AGENT_MODE = os.getenv("AGENT_MODE", "llm")  # llm (openrouter) | router (local)

DATA_FILE = Path(os.getenv("DATA_FILE", "data/claims.json"))

app = FastAPI(title="ClaimAssist API", version="4.0.0")

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
    """Pydantic structured output — the API contract, validated on the way out."""

    answer: str
    claim_id: Optional[str] = None
    status: Optional[str] = None
    confidence: Literal["high", "medium", "low"]


class AgentRequest(BaseModel):
    question: str
    # Human-in-the-loop flag for write actions. The UI sets this to True only
    # after a person clicks Confirm on a rendered draft. It is passed to the
    # agent runner, which overrides the tool argument — the model itself can
    # never set it.
    confirm: bool = False


class AgentResponse(BaseModel):
    answer: str
    tool_trace: List[dict]
    agent_mode: Literal["router", "llm"]


# --- prompt construction --------------------------------------------------------
SYSTEM_BASE = (
    "You are ClaimAssist, an insurance claims-status assistant. Answer the "
    "customer's question briefly, factually and politely. Use ONLY the claim "
    "record provided. If you do not have the information, say so and point "
    "the customer to the claims helpline. Amounts are in INR."
)


def build_messages(req: AskRequest, record: Optional[dict]) -> list:
    system = SYSTEM_BASE
    if record is not None:
        system += "\n\nClaim record (ground truth):\n" + json.dumps(record, indent=2)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": req.question},
    ]


# --- upstream call with retries -------------------------------------------------
async def call_llm(messages: list) -> str:
    """POST to {LLM_BASE_URL}/chat/completions with retry + timeout."""
    url = f"{LLM_BASE_URL}/chat/completions"
    payload = {"model": LLM_MODEL, "messages": messages, "max_tokens": 220}
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
    last_err: Exception = RuntimeError("no attempt made")
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_S) as client:
        for attempt in range(LLM_RETRIES):
            try:
                r = await client.post(url, json=payload, headers=headers)
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
            except (httpx.HTTPError, KeyError) as exc:  # noqa: PERF203
                last_err = exc
                await asyncio.sleep(0.5 * (2**attempt))  # exponential backoff
    raise HTTPException(
        status_code=502,
        detail=f"Upstream LLM unavailable after {LLM_RETRIES} attempts: {last_err}",
    )


# --- endpoints -------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "llm_base_url": LLM_BASE_URL,
        "model": LLM_MODEL,
        "agent_mode": AGENT_MODE,
    }


@app.get("/claims/{claim_id}")
def get_claim(claim_id: str):
    record = CLAIMS.get(claim_id.upper())
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown claim_id {claim_id}")
    return record


# Idempotency-Key pattern (Session 6): retried POSTs return the stored result.
_IDEMPOTENCY_CACHE: Dict[str, dict] = {}


@app.post("/ask", response_model=AskResponse, dependencies=[Depends(require_api_key)])
async def ask(req: AskRequest, idempotency_key: Optional[str] = Header(default=None)):
    if idempotency_key and idempotency_key in _IDEMPOTENCY_CACHE:
        return _IDEMPOTENCY_CACHE[idempotency_key]

    record = None
    if req.claim_id:
        record = CLAIMS.get(req.claim_id.upper())
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown claim_id {req.claim_id}")

    answer = await call_llm(build_messages(req, record))

    if not answer.strip():
        confidence: str = "low"
        answer = "I could not generate an answer. Please contact the claims helpline."
    elif record is not None:
        confidence = "high"
    else:
        confidence = "medium"

    resp = AskResponse(
        answer=answer.strip(),
        claim_id=record["claim_id"] if record else None,
        status=record["status"] if record else None,
        confidence=confidence,
    )
    if idempotency_key:
        _IDEMPOTENCY_CACHE[idempotency_key] = resp.model_dump()
    return resp


@app.post("/ask/stream", dependencies=[Depends(require_api_key)])
async def ask_stream(req: AskRequest):
    """SSE stream of the answer text (media_type text/event-stream)."""
    record = None
    if req.claim_id:
        record = CLAIMS.get(req.claim_id.upper())
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown claim_id {req.claim_id}")
    messages = build_messages(req, record)

    async def event_stream() -> AsyncIterator[str]:
        url = f"{LLM_BASE_URL}/chat/completions"
        payload = {
            "model": LLM_MODEL,
            "messages": messages,
            "max_tokens": 220,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
        for attempt in range(LLM_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=LLM_TIMEOUT_S) as client:
                    async with client.stream("POST", url, json=payload, headers=headers) as r:
                        r.raise_for_status()
                        async for line in r.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[len("data: "):]
                            if data.strip() == "[DONE]":
                                yield "data: [DONE]\n\n"
                                return
                            delta = (
                                json.loads(data)["choices"][0]
                                .get("delta", {})
                                .get("content")
                            )
                            if delta:
                                yield f"data: {json.dumps({'token': delta})}\n\n"
                        yield "data: [DONE]\n\n"
                        return
            except httpx.HTTPError as exc:
                if attempt == LLM_RETRIES - 1:
                    yield f"data: {json.dumps({'error': f'upstream failed: {exc}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                await asyncio.sleep(0.5 * (2**attempt))

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# --- NEW in v4: the agent endpoint ------------------------------------------------
@app.post("/agent", response_model=AgentResponse, dependencies=[Depends(require_api_key)])
async def agent(req: AgentRequest):
    """Single-agent tool use over MCP.

    AGENT_MODE picks the runner; both talk to the same MCP server (a stdio
    child process of this API) and return the same shape, so the UI and the
    tool trace are identical on the local and cloud paths.
    """
    runner = run_agent if AGENT_MODE == "llm" else run_router
    started = time.perf_counter()
    try:
        result = await runner(req.question, confirm=req.confirm)
    except Exception as exc:  # surface agent failures as clean 502s
        raise HTTPException(status_code=502, detail=f"Agent run failed: {exc}")
    result["agent_mode"] = "llm" if AGENT_MODE == "llm" else "router"
    # A one-line audit record of every agent run: who asked what, which tools
    # ran, how long it took. Session 10 upgrades this to full tracing.
    print(json.dumps({
        "event": "agent_run",
        "mode": result["agent_mode"],
        "question": req.question,
        "confirm": req.confirm,
        "tools": [t["tool"] for t in result["tool_trace"]],
        "seconds": round(time.perf_counter() - started, 2),
    }))
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
