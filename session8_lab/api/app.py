# app.py — ClaimAssist API v3 (Session 8)
#
# ClaimAssist v2 (Session 7) routed every LLM call through the LiteLLM proxy.
# v3 adds the two Session 8 features on top of that unchanged plumbing:
#
#   1. RAG WITH CITATIONS — POST /ask now retrieves the top-4 policy clauses
#      from the chroma store built by rag/ingest.py, grounds the LLM in that
#      numbered context, and post-processes the answer into
#      {answer, citations: [{clause_id, doc, snippet}], confidence, not_found}.
#      If the answer is not in the corpus the model must reply NOT_IN_POLICY
#      and the API returns an HONEST structured "not found" response instead
#      of a hallucination.
#   2. MULTIMODAL INTAKE — POST /intake accepts a document image upload.
#      CLOUD path: a vision-capable model (env VISION_MODEL) reads the image
#      via the LiteLLM proxy. LOCAL path: the 0.5B model is text-only, so we
#      fall back to the matching .txt sidecar in data/samples/ and run the
#      SAME extraction prompt over the text — the pattern is identical, only
#      the eyes differ.
#
# Everything else — /claims/{id}, /ask/stream, timeouts, retries, idempotency —
# carries over from v1/v2 unchanged.
import asyncio
import base64
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import AsyncIterator, Literal, Optional

import chromadb
import httpx
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ---- configuration (env only — never hard-coded, Session 2 discipline) -----
# v2/v3 default: the LiteLLM proxy, not the model container directly.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:4000/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "local")
LLM_MODEL = os.environ.get("LLM_MODEL", "openrouter-mini")
# Vision intake via OpenRouter by default. Override VISION_MODEL="" to force
# the local .txt sidecar path, or set LLM_MODEL=qwen-local for offline.
VISION_MODEL = os.environ.get("VISION_MODEL", "openrouter-mini")
CLAIMS_PATH = os.environ.get("CLAIMS_PATH", "data/claims.json")
SAMPLES_DIR = Path(os.environ.get("SAMPLES_DIR", "data/samples"))
CHROMA_DIR = os.environ.get("CHROMA_DIR", "rag/chroma")
RAG_COLLECTION = "policy_clauses"
TOP_K = 4  # clauses retrieved per question

# ---- resilience knobs (unchanged from v1) -----------------------------------
REQUEST_TIMEOUT = httpx.Timeout(90.0, connect=5.0)
MAX_RETRIES = 2
BACKOFF_BASE_S = 0.5

app = FastAPI(title="ClaimAssist API", version="3.0")

with open(CLAIMS_PATH) as f:
    CLAIMS = {c["claim_id"]: c for c in json.load(f)}

# ---- RAG store ----------------------------------------------------------------
# The API process embeds chroma directly (PersistentClient over the directory
# rag/ingest.py wrote). The first query triggers the same ~80 MB ONNX embedding
# model download inside the container — the compose file mounts a volume over
# the cache so it happens once. In production this in-process store becomes a
# vector database SERVICE (pgvector, a managed vector DB) shared by replicas.
_chroma = chromadb.PersistentClient(path=CHROMA_DIR)


def get_collection():
    try:
        return _chroma.get_collection(RAG_COLLECTION)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="RAG store not found — run `python rag/ingest.py` first "
                   f"({type(exc).__name__})",
        )


def retrieve_clauses(question: str) -> list[dict]:
    """Top-K nearest clause chunks for the question.

    Returns [{clause_id, doc, text, distance}] ordered best-first. chroma's
    default space here is L2 over normalised MiniLM embeddings: SMALLER
    distance = closer match. The best (smallest) distance drives `confidence`.
    """
    res = get_collection().query(query_texts=[question], n_results=TOP_K)
    out = []
    for i in range(len(res["ids"][0])):
        out.append(
            {
                "clause_id": res["metadatas"][0][i]["clause_id"],
                "doc": res["metadatas"][0][i]["doc"],
                "text": res["documents"][0][i],
                "distance": res["distances"][0][i],
            }
        )
    return out


# ---- confidence: retrieval distance -> label ----------------------------------
# Thresholds are CORPUS-SPECIFIC and belong in code review, not folklore.
# For this corpus (short clause chunks, MiniLM, L2): a direct clause hit
# ("licence validity") lands well under 1.0; a vaguely related question sits
# around 1.0-1.4; beyond 1.4 retrieval is guessing. Calibrate against a
# labelled question set before trusting these numbers in production —
# Session 3's promptfoo gate is where such assertions live.
CONF_HIGH_MAX = 1.0    # best distance below this  -> "high"
CONF_MEDIUM_MAX = 1.4  # below this                -> "medium"; else "low"


def confidence_from_distance(best_distance: float) -> str:
    if best_distance < CONF_HIGH_MAX:
        return "high"
    if best_distance < CONF_MEDIUM_MAX:
        return "medium"
    return "low"


# ---- Idempotency-Key cache (unchanged from v1) --------------------------------
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
    now = time.time()
    for k in [k for k, (t, _) in _idempotency_cache.items() if now - t > IDEMPOTENCY_TTL_S]:
        _idempotency_cache.pop(k, None)
    _idempotency_cache[key] = (now, response)


# ---- request / response models ------------------------------------------------
class AskRequest(BaseModel):
    question: str
    claim_id: Optional[str] = None


class Citation(BaseModel):
    clause_id: str   # e.g. "M-2.3"
    doc: str         # e.g. "motor_policy.md"
    snippet: str     # first 200 chars of the clause chunk — enough to verify


class AskResponse(BaseModel):
    """v3 structured output: the answer carries its own evidence.

    `citations` lets the UI render verifiable chips; `confidence` is derived
    from retrieval distance (not model vibes); `not_found` is the honest
    NOT_IN_POLICY state — a first-class product state, not an error.
    """
    answer: str
    claim_id: Optional[str] = None
    status: Optional[str] = None
    citations: list[Citation] = []
    confidence: Literal["high", "medium", "low"]
    not_found: bool = False
    request_id: str


class IntakeResult(BaseModel):
    """Structured extraction from an uploaded claim document."""
    document_type: str          # e.g. "garage_estimate", "discharge_summary"
    key_fields: dict            # e.g. {"claim_id": "CLM-1001", "total_inr": 84500}
    source: Literal["vision_model", "local_text_fallback"]
    model: str


# ---- the grounded prompt --------------------------------------------------------
# THE core artefact of Session 8. Three properties, in order of importance:
#   1. "ONLY from the numbered context" — grounding beats the model's frozen
#      training data and its urge to be helpful beyond its evidence.
#   2. "cite clause ids like [M-2.3]" — machine-parseable citations the API
#      extracts with a regex below.
#   3. "reply NOT_IN_POLICY" — an explicit escape hatch. Without one, a model
#      asked about pet insurance will invent a pet-insurance clause.
GROUNDED_SYSTEM = (
    "You are ClaimAssist, an insurance claims-status assistant.\n"
    "Answer the customer's question ONLY from the numbered context below. "
    "Cite the clause ids you used in square brackets, like [M-2.3]. "
    "If the answer is not present in the context, reply with exactly "
    "NOT_IN_POLICY and nothing else. Never invent clauses, amounts or dates."
)


def build_grounded_messages(
    question: str, clauses: list[dict], claim: Optional[dict]
) -> list[dict]:
    context_lines = [
        f"[{c['clause_id']}] ({c['doc']}) {c['text']}" for c in clauses
    ]
    system = GROUNDED_SYSTEM + "\n\nContext:\n" + "\n".join(context_lines)
    if claim is not None:
        # The claim record still grounds record-specific facts (status, dates);
        # the retrieved clauses ground the POLICY reasoning behind them.
        system += "\n\nThe customer's claim record:\n" + json.dumps(claim, indent=2)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]


# ---- citation post-processing ---------------------------------------------------
CITATION_RE = re.compile(r"\[([MHP]-\d+\.\d+)\]")


def extract_citations(answer: str, retrieved: list[dict]) -> list[Citation]:
    """Turn [M-2.3]-style ids in the answer into verifiable Citation objects.

    Only ids that were actually RETRIEVED (or exist in the store) become
    citations — an id the model invented that matches no chunk is dropped,
    which is itself a faithfulness signal.
    """
    by_id = {c["clause_id"]: c for c in retrieved}
    citations: list[Citation] = []
    for cid in dict.fromkeys(CITATION_RE.findall(answer)):  # unique, ordered
        chunk = by_id.get(cid)
        if chunk is None:
            # cited but not in the top-K: look it up directly in the store so
            # a legitimate citation outside the retrieval window still resolves
            got = get_collection().get(ids=[cid])
            if not got["ids"]:
                continue  # invented id — drop it
            chunk = {
                "clause_id": cid,
                "doc": got["metadatas"][0]["doc"],
                "text": got["documents"][0],
            }
        citations.append(
            Citation(clause_id=cid, doc=chunk["doc"], snippet=chunk["text"][:200])
        )
    return citations


NOT_FOUND_ANSWER = "This is not covered in the policy documents I have access to."


def resolve_claim(claim_id: Optional[str]) -> Optional[dict]:
    if claim_id is None:
        return None
    claim = CLAIMS.get(claim_id.strip().upper())
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    return claim


# ---- upstream LLM calls (unchanged mechanics from v1/v2) -----------------------
async def call_llm(messages: list, max_tokens: int = 300, model: str = "") -> str:
    payload = {
        "model": model or LLM_MODEL,
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
                await asyncio.sleep(BACKOFF_BASE_S * (2 ** attempt))
    raise HTTPException(
        status_code=503,
        detail=f"LLM upstream unavailable after {MAX_RETRIES + 1} attempts: "
               f"{type(last_error).__name__}",
    )


async def stream_llm(messages: list, max_tokens: int = 200) -> AsyncIterator[str]:
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
                            continue
                        data = line[len("data: "):]
                        if data == "[DONE]":
                            yield "data: [DONE]\n\n"
                            return
                        chunk = json.loads(data)
                        delta = chunk["choices"][0]["delta"].get("content", "")
                        if delta:
                            sent_any = True
                            yield "data: " + json.dumps({"delta": delta}) + "\n\n"
                    yield "data: [DONE]\n\n"
                    return
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            last_error = exc
            if sent_any:
                break
            if attempt < MAX_RETRIES:
                await asyncio.sleep(BACKOFF_BASE_S * (2 ** attempt))
    yield "data: " + json.dumps(
        {"error": f"LLM upstream unavailable after {MAX_RETRIES + 1} attempts: "
                  f"{type(last_error).__name__}"}
    ) + "\n\n"
    yield "data: [DONE]\n\n"


# ---- endpoints ------------------------------------------------------------------
@app.get("/health")
async def health():
    try:
        rag_chunks = get_collection().count()
    except HTTPException:
        rag_chunks = 0
    return {
        "status": "ok",
        "claims_loaded": len(CLAIMS),
        "rag_chunks": rag_chunks,
        "llm_base_url": LLM_BASE_URL,
        "vision_model": VISION_MODEL or None,
    }


@app.get("/claims/{claim_id}")
async def get_claim(claim_id: str):
    """Raw claim record lookup — 404 when the id is unknown (v1, unchanged)."""
    return resolve_claim(claim_id)


@app.post("/ask", response_model=AskResponse)
async def ask(
    req: AskRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """RAG-grounded, cited, structured answer — the v3 core.

    Pipeline: retrieve top-4 clauses -> grounded prompt (with NOT_IN_POLICY
    escape) -> LLM via the LiteLLM proxy -> post-process: citations extracted
    by regex, confidence from best retrieval distance, honest not-found state.
    """
    if idempotency_key:
        cached = idempotency_get(idempotency_key)
        if cached is not None:
            return AskResponse(**cached)

    claim = resolve_claim(req.claim_id)
    clauses = retrieve_clauses(req.question)
    raw = await call_llm(build_grounded_messages(req.question, clauses, claim))

    if "NOT_IN_POLICY" in raw:
        # The honest path. The model followed its escape hatch; the API turns
        # that into a STRUCTURED product state the UI can render deliberately —
        # in insurance, "I don't know" is a feature and a wrong answer is a
        # liability.
        response = AskResponse(
            answer=NOT_FOUND_ANSWER,
            claim_id=claim["claim_id"] if claim else None,
            status=claim["status"] if claim else None,
            citations=[],
            confidence="low",
            not_found=True,
            request_id="req-" + uuid.uuid4().hex[:12],
        )
    else:
        best_distance = clauses[0]["distance"] if clauses else 999.0
        response = AskResponse(
            answer=raw.strip(),
            claim_id=claim["claim_id"] if claim else None,
            status=claim["status"] if claim else None,
            citations=extract_citations(raw, clauses),
            confidence=confidence_from_distance(best_distance),
            not_found=False,
            request_id="req-" + uuid.uuid4().hex[:12],
        )

    if idempotency_key:
        idempotency_put(idempotency_key, response.model_dump())
    return response


@app.post("/ask/stream")
async def ask_stream(req: AskRequest):
    """SSE streaming answer (v1 behaviour: claim-record grounding).

    Kept claim-grounded on purpose: streaming a CITED answer means emitting
    citation events alongside text deltas (e.g. a `data: {"citation": ...}`
    event type) and resolving them client-side — a very good exercise, left
    as one. The non-streaming /ask above is the reference implementation.
    """
    claim = resolve_claim(req.claim_id)
    system = (
        "You are ClaimAssist, an insurance claims-status assistant. Answer "
        "briefly, factually and politely. Do not invent claim details."
    )
    if claim is not None:
        system += "\n\nThe customer's claim record:\n" + json.dumps(claim, indent=2)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": req.question},
    ]
    return StreamingResponse(
        stream_llm(messages),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- multimodal intake ------------------------------------------------------------
EXTRACTION_PROMPT = (
    "You are an insurance document-intake assistant. The input is a claim "
    "document (for example a garage repair estimate or a hospital discharge "
    "summary). Extract:\n"
    "  document_type: a short snake_case label\n"
    "  key_fields: a flat JSON object of the important fields you can read "
    "(claim id, policy number, names, dates, amounts, diagnosis, ...)\n"
    "Reply with ONLY a JSON object: "
    '{"document_type": "...", "key_fields": {...}}'
)


def parse_extraction(raw: str) -> dict:
    """Best-effort JSON extraction — small models decorate JSON with prose."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and "key_fields" in data:
                return {
                    "document_type": str(data.get("document_type", "unknown")),
                    "key_fields": data["key_fields"]
                    if isinstance(data["key_fields"], dict) else {},
                }
        except json.JSONDecodeError:
            pass
    # Honest degradation: return the raw output rather than fake structure.
    return {"document_type": "unknown", "key_fields": {"raw_model_output": raw[:500]}}


@app.post("/intake", response_model=IntakeResult)
async def intake(file: UploadFile = File(...)):
    """Multimodal document intake: image in, Pydantic-structured fields out.

    CLOUD PATH (env VISION_MODEL set, e.g. openrouter-mini via the proxy): the
    image itself is sent base64-encoded in an image_url content part — the
    OpenAI vision wire format, which LiteLLM translates per provider.

    LOCAL PATH (default): Qwen2.5-0.5B is TEXT-ONLY, so we read the matching
    .txt sidecar in data/samples/ (same filename, .txt extension) and run the
    SAME extraction prompt over the text. The pipeline, prompt and schema are
    identical — only the perception step is substituted. Capability-aware
    product design: teach the pattern locally, light it up with a cloud key.
    """
    image_bytes = await file.read()

    if VISION_MODEL:
        b64 = base64.b64encode(image_bytes).decode()
        mime = file.content_type or "image/png"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": EXTRACTION_PROMPT},
                    {
                        "type": "image_url",
                        # base64 data URL: self-contained, no hosting needed.
                        # For large volumes, providers also accept https URLs —
                        # cheaper to transmit, but the image must be reachable.
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ]
        print(f"[intake] CLOUD path: vision model {VISION_MODEL} reads the image")
        raw = await call_llm(messages, max_tokens=300, model=VISION_MODEL)
        parsed = parse_extraction(raw)
        return IntakeResult(**parsed, source="vision_model", model=VISION_MODEL)

    # ---- LOCAL text fallback (0.5B model is text-only) ----
    sidecar = SAMPLES_DIR / (Path(file.filename or "upload.png").stem + ".txt")
    if not sidecar.exists():
        raise HTTPException(
            status_code=422,
            detail=(
                "Local path needs a text sidecar: no vision model is configured "
                f"(VISION_MODEL unset) and {sidecar} does not exist. Upload one "
                "of the generated samples from data/samples/, or set "
                "VISION_MODEL for real image extraction."
            ),
        )
    print(
        f"[intake] LOCAL text fallback (0.5B model is text-only): "
        f"reading {sidecar} instead of the image"
    )
    doc_text = sidecar.read_text(encoding="utf-8")
    messages = [
        {"role": "user", "content": EXTRACTION_PROMPT + "\n\nDocument:\n" + doc_text}
    ]
    raw = await call_llm(messages, max_tokens=300)
    parsed = parse_extraction(raw)
    return IntakeResult(**parsed, source="local_text_fallback", model=LLM_MODEL)
