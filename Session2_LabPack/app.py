# app.py — Session 2: the same model server, now cloud-ready
# New vs Session 1: /health endpoint (for probes), $PORT support,
# and an API key read from a SECRET (never hard-coded).
import os
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from transformers import pipeline

app = FastAPI()

# Secret comes from the environment — injected by the cloud runtime,
# never baked into the image (writing your ATM PIN on the card!)
API_KEY = os.environ.get("API_KEY", "")

# load ONCE at startup
generator = pipeline("text-generation", model="Qwen/Qwen2.5-0.5B-Instruct")


class Prompt(BaseModel):
    text: str
    max_tokens: int = 64


@app.get("/health")
def health():
    """Probe target: answers only when the model is loaded and ready."""
    return {"status": "ok"}


@app.post("/generate")
def generate(req: Prompt, x_api_key: str = Header(default="")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
    out = generator(req.text, max_new_tokens=req.max_tokens)
    return {"answer": out[0]["generated_text"]}
