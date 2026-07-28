"""ClaimAssist — local OpenAI-compatible model server (llm-server:openai).

Wraps Qwen/Qwen2.5-0.5B-Instruct (Hugging Face transformers) behind the two
endpoints the OpenAI ecosystem expects:

    GET  /health               -> {"status": "ok"}
    POST /v1/chat/completions  -> OpenAI-shaped JSON, stream and non-stream

Because the wire format is the OpenAI protocol, this small container is a
drop-in backend for the openai SDK, for LiteLLM, and for the Vercel AI SDK —
the "OpenAI protocol as lingua franca" thread of this course.

STREAMING NOTE: on CPU we generate the FULL completion first, then stream it
out word-by-word as SSE chunks. That is simple and reliable for a lab.
Production inference servers such as vLLM stream true token-by-token as the
model decodes — the wire protocol below is identical either way.
"""

import json
import os
import time
import uuid
from typing import List, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

MODEL_ID = os.getenv("MODEL_ID", "Qwen/Qwen2.5-0.5B-Instruct")
PORT = int(os.getenv("PORT", "8090"))

app = FastAPI(title="llm-server:openai", version="2.0.0")

_pipe = None
_tokenizer = None


def _load():
    """Load the model once. Called at startup so the first request is fast."""
    global _pipe, _tokenizer
    if _pipe is None:
        from transformers import AutoTokenizer, pipeline

        _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        _pipe = pipeline(
            "text-generation", model=MODEL_ID, tokenizer=_tokenizer, device=-1
        )
    return _pipe, _tokenizer


@app.on_event("startup")
def startup() -> None:
    _load()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "qwen-local"
    messages: List[ChatMessage]
    max_tokens: Optional[int] = 256
    temperature: Optional[float] = 0.7
    stream: bool = False


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest):
    pipe, tokenizer = _load()

    # The tokenizer's chat template turns the OpenAI messages array into the
    # exact prompt format the model was instruction-tuned on.
    prompt = tokenizer.apply_chat_template(
        [m.model_dump() for m in req.messages],
        tokenize=False,
        add_generation_prompt=True,
    )

    do_sample = (req.temperature or 0) > 0
    out = pipe(
        prompt,
        max_new_tokens=req.max_tokens or 256,
        do_sample=do_sample,
        temperature=max(req.temperature or 0.7, 1e-3) if do_sample else None,
        return_full_text=False,
    )
    text = (out[0]["generated_text"] or "").strip()

    completion_id = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    # Usage is counted with the real tokenizer, exactly as OpenAI reports it.
    prompt_tokens = len(tokenizer.encode(prompt))
    completion_tokens = len(tokenizer.encode(text)) if text else 0

    if not req.stream:
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": req.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }
        )

    # ---- stream=true: Server-Sent Events, OpenAI chunk format -------------
    def sse():
        def chunk(delta, finish_reason=None):
            return "data: " + json.dumps(
                {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": req.model,
                    "choices": [
                        {"index": 0, "delta": delta, "finish_reason": finish_reason}
                    ],
                }
            ) + "\n\n"

        # First chunk carries the role, as the OpenAI API does.
        yield chunk({"role": "assistant", "content": ""})
        words = text.split(" ")
        for i, word in enumerate(words):
            piece = word if i == len(words) - 1 else word + " "
            yield chunk({"content": piece})
            time.sleep(0.02)  # visible streaming pace for the lab
        yield chunk({}, finish_reason="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
