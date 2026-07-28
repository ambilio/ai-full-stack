# app.py — ClaimAssist API v1 (Session 6)
#
# The first production version of ClaimAssist, the claims-status copilot built
# across Sessions 6-10. Four production patterns are demonstrated here:
#
#   1. STRUCTURED OUTPUT  — POST /ask returns a Pydantic-validated model
#                           {answer, claim_id, status, confidence}, not free text.
#   2. STREAMING          — POST /ask/stream sends the answer as Server-Sent
#                           Events (SSE), forwarding chunks from the model server.
#   3. RESILIENCE         — every upstream LLM call has an explicit timeout and
#                           a bounded retry loop with exponential backoff.
#   4. IDEMPOTENCY        — POST /ask honours an Idempotency-Key header, so a
#                           retried request cannot run (and bill) twice.
#
# The app speaks the OpenAI protocol to whatever LLM_BASE_URL points at: the
# local llm-server:openai container today, any cloud provider tomorrow, the
# LiteLLM proxy in Session 7. Nothing in this file changes when it switches.
import asyncio
import json
import os
import time
import uuid
from typing import AsyncIterator, Literal, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ---- configuration (env only — never hard-coded, Session 2 discipline) -----
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:8090/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "local")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen-local")
CLAIMS_PATH = os.environ.get("CLAIMS_PATH", "data/claims.json")

# ---- resilience knobs -------------------------------------------------------
# NEVER call an LLM without a timeout: the default is an infinite hang, and a
# hung upstream will eventually exhaust every worker in the service.
REQUEST_TIMEOUT = httpx.Timeout(90.0, connect=5.0)  # generous total, tight connect
MAX_RETRIES = 2            # 1 attempt + 2 retries, then fail loudly
BACKOFF_BASE_S = 0.5       # exponential backoff: 0.5 s, then 1.0 s

app = FastAPI(title="ClaimAssist API", version="1.0")

with open(CLAIMS_PATH) as f:
    CLAIMS = {c["claim_id"]: c for c in json.load(f)}

# ---- Idempotency-Key cache ---------------------------------------------------
# In-memory dict of recent keys -> (stored_at, cached response). Good enough
# for a single process. IN PRODUCTION this is Redis (SET key NX EX ttl): the
# cache must be shared across replicas and survive restarts, or two replicas
# can both execute the "same" request.
IDEMPOTENCY_TTL_S = 600
_idempotency_cache: dict[str, tuple[float, dict]] = {}


def idempotency_get(key: str) -> Optional[dict]:
    entry = _idempotency_cache.get(key)
    if entry is None:
        return None
    stored_at, response = entry
    if time.time() - stored_at > IDEMPOTENCY_TTL_S:
        _idempotency_cache.pop(key, None)
        return None
    return response


def idempotency_put(key: str, response: dict) -> None:
    # opportunistic eviction of expired entries keeps the dict bounded
    now = time.time()
    for k in [k for k, (t, _) in _idempotency_cache.items() if now - t > IDEMPOTENCY_TTL_S]:
        _idempotency_cache.pop(k, None)
    _idempotency_cache[key] = (now, response)


# ---- request / response models ----------------------------------------------
class AskRequest(BaseModel):
    question: str
    claim_id: Optional[str] = None


class AskResponse(BaseModel):
    """Structured output: product features consume FIELDS, not prose.

    The UI renders `answer`, badges `confidence`, and links `claim_id` —
    none of which is possible if the endpoint returns a blob of free text.
    FastAPI validates this model on the way out (response_model below).
    """
    answer: str
    claim_id: Optional[str] = None
    status: Optional[str] = None
    confidence: Literal["high", "medium", "low"]
    request_id: str  # unique per execution — the idempotency proof in the lab


# ---- prompt construction: claim grounding ------------------------------------
SYSTEM_BASE = (
    "You are ClaimAssist, an insurance claims-status assistant for call-centre "
    "deflection. Answer the customer's question briefly, factually and politely. "
    "Do not invent claim details, amounts or dates."
)


def build_messages(question: str, claim: Optional[dict]) -> list[dict]:
    """Ground the LLM by injecting the claim record JSON into the system prompt.

    v1 grounding = one claim record. Session 8 adds retrieval over the policy
    documents (RAG) so the model can also cite policy clauses.
    """
    if claim is not None:
        system = (
            SYSTEM_BASE
            + "\n\nThe customer's claim record is below. Answer ONLY from this "
            + "record; if the record does not contain the answer, say so.\n\n"
            + json.dumps(claim, indent=2)
        )
    else:
        system = (
            SYSTEM_BASE
            + "\n\nNo claim record is selected. If the question needs claim "
            + "details, ask the customer for their claim id (format CLM-XXXX)."
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]


def resolve_claim(claim_id: Optional[str]) -> Optional[dict]:
    if claim_id is None:
        return None
    claim = CLAIMS.get(claim_id.strip().upper())
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    return claim


# ---- upstream LLM calls: timeout + bounded retries with backoff --------------
# The openai SDK is a drop-in alternative for this block (same protocol):
#     from openai import AsyncOpenAI
#     client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY,
#                          timeout=90.0, max_retries=2)
# We use raw httpx here so the timeout/retry mechanics stay visible — the
# SDK's max_retries does exactly what this loop does.
async def call_llm(messages: list[dict], max_tokens: int = 200) -> str:
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
    last_error: Optional[Exception] = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                r = await client.post(
                    f"{LLM_BASE_URL}/chat/completions", json=payload, headers=headers
                )
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                # exponential backoff: 0.5 s, 1.0 s — never hammer a sick upstream
                await asyncio.sleep(BACKOFF_BASE_S * (2 ** attempt))
    raise HTTPException(
        status_code=503,
        detail=f"LLM upstream unavailable after {MAX_RETRIES + 1} attempts: "
               f"{type(last_error).__name__}",
    )


async def stream_llm(messages: list[dict], max_tokens: int = 200) -> AsyncIterator[str]:
    """Stream the answer from the model server, re-emitting simplified SSE.

    Upstream events are OpenAI chat.completion.chunk JSON; downstream we emit
    `data: {"delta": "..."}` events (clients only need the text) terminated by
    `data: [DONE]`.

    Retry policy for streams: retries are safe only BEFORE the first byte has
    been sent to the client. Once chunks have gone out, a retry would repeat
    text — so a mid-stream failure is reported IN-BAND as an error event.
    """
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
    last_error: Optional[Exception] = None
    sent_any = False
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                async with client.stream(
                    "POST", f"{LLM_BASE_URL}/chat/completions",
                    json=payload, headers=headers,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue  # SSE keep-alives / blank separators
                        data = line[len("data: "):]
                        if data == "[DONE]":
                            yield "data: [DONE]\n\n"
                            return
                        chunk = json.loads(data)
                        delta = chunk["choices"][0]["delta"].get("content", "")
                        if delta:
                            sent_any = True
                            yield "data: " + json.dumps({"delta": delta}) + "\n\n"
                    yield "data: [DONE]\n\n"  # upstream closed cleanly
                    return
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            last_error = exc
            if sent_any:
                break  # mid-stream failure: do NOT retry (would repeat text)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(BACKOFF_BASE_S * (2 ** attempt))
    # SSE responses have already committed a 200 status line, so failures must
    # be signalled in-band. Clients (curl, the Streamlit UI) handle this event.
    yield "data: " + json.dumps(
        {"error": f"LLM upstream unavailable after {MAX_RETRIES + 1} attempts: "
                  f"{type(last_error).__name__}"}
    ) + "\n\n"
    yield "data: [DONE]\n\n"


# ---- endpoints ---------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "claims_loaded": len(CLAIMS), "llm_base_url": LLM_BASE_URL}


@app.get("/claims/{claim_id}")
async def get_claim(claim_id: str):
    """Raw claim record lookup — 404 when the id is unknown."""
    return resolve_claim(claim_id)


@app.post("/ask", response_model=AskResponse)
async def ask(
    req: AskRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Non-streaming, structured answer about a claim.

    With an Idempotency-Key header, a repeated request returns the CACHED
    response — same request_id — instead of executing (and paying for) a
    second LLM call. This is how retried POSTs are made safe.
    """
    if idempotency_key:
        cached = idempotency_get(idempotency_key)
        if cached is not None:
            return AskResponse(**cached)  # replay: identical request_id

    claim = resolve_claim(req.claim_id)
    answer = await call_llm(build_messages(req.question, claim))

    response = AskResponse(
        answer=answer,
        claim_id=claim["claim_id"] if claim else None,
        status=claim["status"] if claim else None,
        # v1 heuristic: an answer grounded in a claim record is high-confidence;
        # an ungrounded answer is low. Session 8 replaces this with a mapping
        # from retrieval distance.
        confidence="high" if claim else "low",
        request_id="req-" + uuid.uuid4().hex[:12],
    )
    if idempotency_key:
        idempotency_put(idempotency_key, response.model_dump())
    return response


@app.post("/ask/stream")
async def ask_stream(req: AskRequest):
    """SSE streaming answer: time-to-first-token is the UX metric that matters."""
    claim = resolve_claim(req.claim_id)
    return StreamingResponse(
        stream_llm(build_messages(req.question, claim)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
