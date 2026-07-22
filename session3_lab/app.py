# app.py — Session 3: versioned model server for canary deployments
# New vs Session 2: VERSION and PROMPT_PREFIX come from deploy-time config,
# so "deploying v2" = same image + different configuration.
import os
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from transformers import pipeline

app = FastAPI()

API_KEY = os.environ.get("API_KEY", "")
VERSION = os.environ.get("VERSION", "v1")
# The server-side prompt policy. Changing this is a "model behavior release"
# and must pass the eval gate before it takes traffic.
PROMPT_PREFIX = os.environ.get("PROMPT_PREFIX", "")

generator = pipeline("text-generation", model="Qwen/Qwen2.5-0.5B-Instruct")


class Prompt(BaseModel):
    text: str
    max_tokens: int = 80


@app.get("/health")
def health():
    return {"status": "ok", "version": VERSION}


@app.get("/version")
def version():
    """Used in the lab to see WHICH version served your request via the LB."""
    return {"version": VERSION}


@app.post("/generate")
def generate(req: Prompt, x_api_key: str = Header(default="")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
    out = generator(PROMPT_PREFIX + req.text, max_new_tokens=req.max_tokens)
    return {"answer": out[0]["generated_text"], "version": VERSION}
