# app.py — Session 5: versioned model server with Langfuse tracing.
# Same v1/v2 pattern as Session 3 (VERSION and PROMPT_PREFIX are deploy-time
# configuration), PLUS: every /generate call emits a Langfuse trace tagged
# with the model version, so v1 and v2 can be compared side by side in the UI.
import os
import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from transformers import pipeline

app = FastAPI()

API_KEY = os.environ.get("API_KEY", "")
VERSION = os.environ.get("VERSION", "v1")
# The server-side prompt policy. Changing this is a "model behavior release".
PROMPT_PREFIX = os.environ.get("PROMPT_PREFIX", "")
MODEL_NAME = "Qwen/Qwen2.5-0.5B-Instruct"

# ---- Langfuse init: graceful no-op when keys are absent -------------------
# The lab starts this stack BEFORE Langfuse keys exist. Tracing must be an
# optional add-on: if the LANGFUSE_* variables are unset, or the SDK cannot
# be imported or initialised, the service still runs — it just emits nothing.
langfuse = None
try:
    if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
        from langfuse import Langfuse

        # Reads LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST from env.
        langfuse = Langfuse()
        print(f"[{VERSION}] Langfuse tracing ENABLED -> "
              f"{os.environ.get('LANGFUSE_HOST', 'https://cloud.langfuse.com')}")
    else:
        print(f"[{VERSION}] LANGFUSE keys not set — tracing disabled (no-op)")
except Exception as exc:  # SDK missing, bad keys, host unreachable — never fatal
    print(f"[{VERSION}] Langfuse init failed — tracing disabled: {exc}")
    langfuse = None

generator = pipeline("text-generation", model=MODEL_NAME)


class Prompt(BaseModel):
    text: str
    max_tokens: int = 80


@app.get("/health")
def health():
    return {"status": "ok", "version": VERSION}


@app.get("/version")
def version():
    return {"version": VERSION}


@app.post("/generate")
def generate(req: Prompt, x_api_key: str = Header(default="")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    started = time.time()
    out = generator(PROMPT_PREFIX + req.text, max_new_tokens=req.max_tokens)
    answer = out[0]["generated_text"]
    latency_ms = round((time.time() - started) * 1000)

    # ---- Emit one trace per request. The tag "model_version:vN" is what the
    # lab filters on in the Langfuse UI to compare v1 against v2. Tracing
    # failures are logged and swallowed — observability must never take the
    # serving path down.
    if langfuse is not None:
        try:
            trace = langfuse.trace(
                name="generate",
                input=req.text,
                output=answer,
                tags=[f"model_version:{VERSION}"],
                metadata={"model_version": VERSION, "latency_ms": latency_ms},
            )
            trace.generation(
                name="qwen-generate",
                model=MODEL_NAME,
                input=PROMPT_PREFIX + req.text,
                output=answer,
                metadata={
                    "model_version": VERSION,
                    "latency_ms": latency_ms,
                    "prompt_prefix": PROMPT_PREFIX,
                    "max_tokens": req.max_tokens,
                },
            )
        except Exception as exc:
            print(f"[{VERSION}] trace emit failed (non-fatal): {exc}")

    return {"answer": answer, "version": VERSION, "latency_ms": latency_ms}
