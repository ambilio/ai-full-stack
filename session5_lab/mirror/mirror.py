# mirror.py — Session 5: the traffic mirror (shadow deployment).
#
# Every POST /generate is:
#   1. forwarded to v1 (stable)      -> its response is returned to the caller;
#   2. duplicated to v2 (candidate)  -> fire-and-forget: the coroutine runs in
#      the background via asyncio.create_task, its result is ONLY logged.
#
# Users therefore experience v1 exactly as before, while v2 receives the full
# production traffic distribution at zero user exposure. This is the local
# equivalent of service-mesh traffic mirroring (e.g. Istio HTTPRoute mirror).
import asyncio
import logging
import os
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s mirror %(message)s")
log = logging.getLogger("mirror")

V1_URL = os.environ.get("V1_URL", "http://llm-v1:8080/generate")
V2_URL = os.environ.get("V2_URL", "http://llm-v2:8080/generate")

app = FastAPI()
# One long-lived async client: connection pooling, generous timeout for CPU inference.
client = httpx.AsyncClient(timeout=httpx.Timeout(180.0))


async def shadow_to_v2(body: dict, headers: dict) -> None:
    """Send the mirrored copy to the candidate. The result is only logged —
    it is never returned to a user, and a v2 failure never affects the caller."""
    started = time.time()
    try:
        r = await client.post(V2_URL, json=body, headers=headers)
        log.info("SHADOW -> v2 status=%s latency_ms=%d",
                 r.status_code, round((time.time() - started) * 1000))
    except Exception as exc:
        log.warning("SHADOW -> v2 FAILED (users unaffected): %s", exc)


@app.get("/health")
def health():
    return {"status": "ok", "role": "mirror"}


@app.post("/generate")
async def generate(request: Request):
    body = await request.json()
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": request.headers.get("x-api-key", ""),
    }

    # Fire-and-forget the shadow copy — NOT awaited on the response path.
    asyncio.create_task(shadow_to_v2(body, headers))

    # The primary call: v1's answer is what the user receives, always.
    started = time.time()
    r = await client.post(V1_URL, json=body, headers=headers)
    log.info("PRIMARY -> v1 status=%s latency_ms=%d",
             r.status_code, round((time.time() - started) * 1000))
    return JSONResponse(status_code=r.status_code, content=r.json())
