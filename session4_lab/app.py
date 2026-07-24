# app.py — Session 4: the model server, now OBSERVABLE and AUDITABLE.
# New vs Session 3:
#   1. Prometheus metrics (request counter + latency histogram) at /metrics
#   2. Audit log: every /generate call written as a JSON line, with PII
#      redacted BEFORE the log is written (redaction at log time).
import json
import os
import re
import time
import uuid

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel
from transformers import pipeline
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

app = FastAPI()

API_KEY = os.environ.get("API_KEY", "")
MODEL_VERSION = os.environ.get("VERSION", "v1")
AUDIT_PATH = os.environ.get("AUDIT_PATH", "/app/logs/audit.jsonl")

generator = pipeline("text-generation", model="Qwen/Qwen2.5-0.5B-Instruct")

# ---------------- Prometheus instrumentation ----------------
REQUESTS = Counter(
    "llm_requests_total", "Total HTTP requests",
    ["endpoint", "status"],
)
LATENCY = Histogram(
    "llm_request_latency_seconds", "Request latency in seconds",
    ["endpoint"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32),
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Times EVERY request and records endpoint + status code."""
    t0 = time.perf_counter()
    response = await call_next(request)
    dt = time.perf_counter() - t0
    path = request.url.path
    if path in ("/generate", "/health"):        # keep label cardinality bounded
        REQUESTS.labels(endpoint=path, status=str(response.status_code)).inc()
        LATENCY.labels(endpoint=path).observe(dt)
    return response


@app.get("/metrics")
def metrics():
    """Prometheus scrapes this endpoint. Plain text, one metric per line."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------- PII redaction (at log time) ----------------
# Deterministic regex redaction for the lab. Production systems typically use
# an NER-based engine (e.g. Microsoft Presidio) on top of patterns like these.
PII_PATTERNS = [
    ("EMAIL",   re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("AADHAAR", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("PAN",     re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("PHONE",   re.compile(r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b")),
]


def redact(text: str) -> str:
    for name, pattern in PII_PATTERNS:
        text = pattern.sub(f"<{name}_REDACTED>", text)
    return text


def audit_log(entry: dict):
    """Append-only JSON-lines audit trail. One line per model interaction."""
    os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
    with open(AUDIT_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------- API ----------------
class Prompt(BaseModel):
    text: str
    max_tokens: int = 80


@app.get("/health")
def health():
    return {"status": "ok", "version": MODEL_VERSION}


@app.post("/generate")
def generate(req: Prompt, request: Request, x_api_key: str = Header(default="")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    t0 = time.perf_counter()
    out = generator(req.text, max_new_tokens=req.max_tokens)
    answer = out[0]["generated_text"]
    latency_ms = round((time.perf_counter() - t0) * 1000)

    # Regulator-grade record: who/when/what/which model — PII already redacted.
    audit_log({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "request_id": str(uuid.uuid4()),
        "client": request.client.host if request.client else "unknown",
        "model_version": MODEL_VERSION,
        "prompt_redacted": redact(req.text),
        "response_redacted": redact(answer),
        "latency_ms": latency_ms,
        "max_tokens": req.max_tokens,
    })
    return {"answer": answer, "version": MODEL_VERSION}
