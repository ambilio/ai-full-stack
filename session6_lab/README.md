# Session 6 Lab — ClaimAssist v1: Production AI API + Streamlit Chat

**Track 2.B · Session 1 (Day 6) — Production AI APIs & Application Frameworks**

## Goal

Ship **ClaimAssist v1**, the first version of the insurance claims-status
copilot you will build across Sessions 6-10:

- an **OpenAI-compatible model server** (`llm-server:openai`, port **8090**) —
  the local Qwen container from Track 2.A, now speaking
  `POST /v1/chat/completions` (non-stream and SSE stream);
- the **ClaimAssist API** (FastAPI, port **8000**) — claim lookup, a
  Pydantic-structured `/ask`, an SSE `/ask/stream`, timeouts + retries with
  backoff, and an Idempotency-Key pattern;
- a **Streamlit chat UI** (port **8501**) that streams tokens from the API.

Time budget: **~60 minutes** (Step 0: 5 · Step 1: 10 · Step 2: 10 ·
Step 3: 10 · Step 4: 10 · Step 5: 10 · Step 6: 5).

## Architecture

```
      streamlit run ui/chat_app.py                 docker compose
 ┌───────────────────┐          ┌────────────────────┐          ┌───────────────────────┐
 │  Streamlit chat   │   HTTP   │  ClaimAssist API   │  OpenAI  │   llm-server:openai   │
 │  (host, :8501)    ├─────────►│  FastAPI  (:8000)  ├─────────►│   Qwen shim (:8090)   │
 │  st.chat_message  │   SSE    │  GET /claims/{id}  │  proto   │  /v1/chat/completions │
 │  st.chat_input    │◄─────────┤  POST /ask         │◄─────────┤  /health              │
 └───────────────────┘          │  POST /ask/stream  │   SSE    └───────────────────────┘
                                └─────────┬──────────┘
                                          │ reads (read-only mount)
                                   data/claims.json
```

The UI runs **on the host**, not in compose: a hot-reloading dev UI gains
nothing from a container, and this keeps the compose file focused on the two
deployable services. (Session 7 brings the production web front end.)

---

## Step 0 — Setup (5 min)

```bash
cd session6_lab

# .env — the local/cloud switch (echo-based so it works everywhere; or: cp .env.example .env)
echo 'LLM_BASE_URL=http://model:8090/v1' >  .env
echo 'LLM_API_KEY=local'                 >> .env
echo 'LLM_MODEL=qwen-local'              >> .env

# venv for the Streamlit UI (the only thing that runs on the host)
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r ui/requirements.txt # streamlit + requests

docker --version && docker compose version
```

> `http://model:8090/v1` is the model server's address **inside** the compose
> network (service DNS). From your host shell the same server is
> `http://localhost:8090/v1` — you will curl it that way in Step 1.

---

## Step 1 — Start the stack and speak the OpenAI protocol raw (10 min)

```bash
docker compose up -d --build
docker compose ps        # wait until model AND api show (healthy) — model load takes ~2 min
# macOS: no `watch` by default — `brew install watch`, or just re-run `docker compose ps`
```

**See the OpenAI wire format with your own eyes** — a raw curl, no SDK:

```bash
curl -s localhost:8090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen-local",
       "messages":[{"role":"user","content":"What is an insurance claim?"}],
       "max_tokens":60}' | python3 -m json.tool
```

Note the shape: `choices[0].message.content` and `usage` with real token
counts from the tokenizer. Every OpenAI-compatible provider on earth returns
this same JSON. Now the streaming variant — `-N` disables curl's buffering:

```bash
curl -N localhost:8090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen-local",
       "messages":[{"role":"user","content":"What is an insurance claim?"}],
       "max_tokens":60, "stream":true}'
```

Watch the `data: {...delta...}` events arrive one by one, terminated by
`data: [DONE]`. That is Server-Sent Events — the protocol every LLM chat UI
is built on.

---

## Step 2 — Explore the ClaimAssist API (10 min)

```bash
# raw claim lookup
curl -s localhost:8000/claims/CLM-1002 | python3 -m json.tool
# 404 handling
curl -s -i localhost:8000/claims/CLM-9999 | head -1

# structured question answering — the LLM grounded with the claim record
curl -s localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"What is the status of my claim and when will I be paid?",
       "claim_id":"CLM-1002"}' | python3 -m json.tool
```

Inspect the structured JSON: `{answer, claim_id, status, confidence,
request_id}`. The `confidence` field is a **product feature** — the UI can
badge it, downstream systems can filter on it. None of that is possible with
a blob of free text. (In v1 confidence is a simple grounded/ungrounded
heuristic; Session 8 derives it from retrieval distance.)

---

## Step 3 — SSE deep-dive: streaming, failure and retry (10 min)

```bash
curl -N localhost:8000/ask/stream \
  -H "Content-Type: application/json" \
  -d '{"question":"Why was my claim rejected and what can I do now?",
       "claim_id":"CLM-1003"}'
```

The API forwards chunks from the model server as simplified
`data: {"delta": "..."}` events. Now break it. Run the curl again, and **in a
second terminal, mid-request**:

```bash
docker kill claimassist-model
```

What you observe depends on timing, and both outcomes are the lesson:

- **Killed before the first chunk** (during generation): the API's retry loop
  engages — watch the pause (0.5 s, then 1 s backoff) — and after 3 failed
  attempts the stream ends with an **in-band error event**
  `data: {"error": "LLM upstream unavailable after 3 attempts: ..."}`.
- **Killed mid-stream** (chunks already flowing): no retry — replaying would
  repeat text — the stream ends immediately with the in-band error event.

A streaming response has already committed its `200 OK`, so failures **must**
travel in-band; clients have to handle error events. Restart and verify
recovery:

```bash
docker compose up -d model
docker compose ps            # wait for (healthy), then re-run the first curl
```

---

## Step 4 — Idempotency: retried POSTs must not execute twice (10 min)

```bash
KEY="demo-key-001"
curl -s localhost:8000/ask \
  -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" \
  -d '{"question":"Where is my claim?","claim_id":"CLM-1005"}' | python3 -m json.tool

# run the EXACT same command again
curl -s localhost:8000/ask \
  -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" \
  -d '{"question":"Where is my claim?","claim_id":"CLM-1005"}' | python3 -m json.tool
```

**Proof:** the second response returns instantly and its `request_id` is
**identical** to the first — it is the cached response, not a second
execution. Without the key, a client retry after a network blip would run the
LLM (and the bill) twice; with it, retries are safe. The lab cache is an
in-memory dict; in production it is Redis (`SET key NX EX ttl`) shared across
replicas — see the comment in `api/app.py`.

---

## Step 5 — The Streamlit chat (10 min)

```bash
source .venv/bin/activate      # if not already active
streamlit run ui/chat_app.py   # opens http://localhost:8501
```

Pick a claim in the sidebar and chat:

- select **CLM-1005** → "Where is my claim?" — grounded status answer,
  streamed token by token via `/ask/stream`;
- select **CLM-1003** → "Why was my claim rejected?" — the record's notes
  cite clause M-2.3 (licence expired on the accident date);
- now ask "What exactly does clause M-2.3 say?" — **v1 cannot answer this
  well**: it only sees the claim record, not the policy documents. Policy
  questions with citations are Session 8 (RAG over `data/policies/`).

---

## Step 6 — Framework comparison discussion (5 min)

Discuss in pairs, referring to what you just built:

| Dimension | Streamlit (today) | Next.js + Vercel AI SDK (Session 7) |
|---|---|---|
| Time to working chat UI | ~90 lines, one afternoon | Scaffold + components, days |
| Best fit | Internal tools, analytics teams, demos | Customer-facing products |
| Customisation / branding | Limited (theme knobs) | Total (it is your front end) |
| Auth, SEO, routing | Bolt-on, awkward | First-class |
| Streaming UX control | `st.write_stream` (good enough) | Full control per token/component |
| Ops | One Python process | Node build, CDN, edge — real web ops |

The point is honest fit, not a winner: Streamlit is the right tool for the
internal claims-ops dashboard; it is the wrong tool for the public customer
portal. Session 7 builds the latter.

---

## Cloud path (optional)

> Set `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` in `.env` to **any
> OpenAI-compatible provider** — nothing else changes. That is the point of
> the protocol.

```bash
# .env
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-your-key-here
LLM_MODEL=gpt-4o-mini
```

```bash
docker compose up -d api   # recreate the api with the new env
```

Every step above (2-5) works identically — same endpoints, same structured
JSON, same SSE stream, just better answers and real per-token streaming. You
can even skip starting the local model (`docker compose up -d --no-deps api`).
From Session 7, a LiteLLM proxy makes this switch dynamic (routing,
fallbacks) instead of an env edit.

---

## Local → production mapping

| In this lab | In production |
|---|---|
| `llm-server:openai` container (full text, then chunked SSE) | vLLM / TGI (true token-by-token) or a managed endpoint (OpenAI, Azure, Bedrock) |
| `LLM_BASE_URL` in `.env` | Provider config / LiteLLM proxy with routing + fallbacks (Session 7) |
| In-memory Idempotency-Key dict | Redis `SET NX EX`, shared across replicas |
| Manual httpx retry loop with backoff | SDK `max_retries` / mesh retry policy — same contract |
| compose `healthcheck` + `depends_on: service_healthy` | Kubernetes liveness/readiness probes + rollout ordering |
| SSE from FastAPI `StreamingResponse` | Same protocol through a gateway/CDN with buffering disabled |
| Streamlit on the host | Internal tool behind SSO; Next.js for customer-facing (Session 7) |

## Deliverables

1. **Wire-format evidence** — transcripts of the non-streaming and streaming
   curls against `:8090/v1/chat/completions` (Step 1).
2. **Structured output** — the `/ask` JSON for CLM-1002 showing all five
   fields including `confidence` (Step 2).
3. **Failure-handling evidence** — the `/ask/stream` transcript showing the
   retry pause and/or the in-band error event, plus the successful re-run
   after restart (Step 3).
4. **Idempotency proof** — both `/ask` responses with the same
   `Idempotency-Key` showing identical `request_id` (Step 4).
5. **UI evidence** — a screenshot of the Streamlit chat answering
   "Why was CLM-1003 rejected?" (Step 5).
6. **Written answer** — three sentences: when is Streamlit the right choice,
   when is it the wrong one, and what specifically does Session 7's stack add?

Keep this directory — Sessions 7-10 evolve this same application.
