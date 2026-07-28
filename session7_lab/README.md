# Session 7 Lab — ClaimAssist v2: Provider Abstraction with LiteLLM & a Streaming Production UI

**Track 2.B · Session 2 (Day 7) · ~60 minutes hands-on**

## Goal

Upgrade ClaimAssist from v1 (Session 6) to v2:

1. Put a **LiteLLM proxy** between the app and the model — one OpenAI-compatible
   endpoint in, any provider out (local Qwen, OpenAI, Azure, Bedrock, Vertex).
2. Re-point the ClaimAssist API at the proxy — **a one-line env change**.
3. Add a **production-grade streaming chat UI** in Next.js 15 with the Vercel
   AI SDK (`useChat` + `streamText`).
4. Break the local model on purpose and watch **retries, clean errors, and
   (with a cloud key) automatic failover** to another provider mid-conversation.

This pack is standalone — everything from v1 (model server, API, data) is
included. Same app, same data, same ports as every ClaimAssist session.

## Architecture

```
                          ┌────────────────────────── docker compose ─────────────────────────┐
 Browser ── :3001 ──┐     │                                                                    │
                    ▼     │                                                                    │
 ┌──────────────────────┐ │  ┌──────────────────┐      ┌──────────────────────────────┐       │
 │ Next.js 15 (web/)    │ │  │ LiteLLM proxy    │────▶ │ model  :8090                 │       │
 │ useChat ↔ route.ts   │─┼─▶│ :4000            │      │ llm-server:openai (Qwen 0.5B)│       │
 └──────────────────────┘ │  │ litellm/config   │      └──────────────────────────────┘       │
                          │  │ .yaml            │ ---▶  cloud: OpenAI / Azure / Bedrock /     │
 ┌──────────────────────┐ │  │ retries+fallback │       Vertex  (CLOUD PATH, optional)        │
 │ ClaimAssist API :8000│─┼─▶└──────────────────┘                                             │
 │ /claims /ask /stream │ │        ▲ the ONLY thing that changed for the API:                 │
 └──────────────────────┘ │          LLM_BASE_URL now points here (:4000, was :8090)          │
                          └────────────────────────────────────────────────────────────────────┘
```

Every arrow speaks the same OpenAI protocol (`POST /v1/chat/completions`).

---

## Step 0 — Setup (8 min)

```bash
cd session7_lab

# environment file (cross-platform)
cp .env.example .env          # Windows PowerShell: Copy-Item .env.example .env

# Node.js 18+ is required for the Next.js app
node --version                # expect v18 or newer

# install web dependencies now so they download while compose builds (~2 min)
cd web && npm install && cd ..
```

Notes:
- If `npm install` reports a peer-dependency conflict, run
  `npm install --legacy-peer-deps` — the pinned versions are compatible.
- Docker Desktop must be running; ~6 GB free RAM recommended.
- macOS users: install `watch` via `brew install watch` if you want it later
  (optional; every step below works without it).

## Step 1 — Start the stack and verify the PROXY (12 min)

```bash
docker compose up -d --build
docker compose ps        # wait until model is "healthy" (first run downloads ~1 GB)
```

The proxy is the new component — verify it first. List its models:

```bash
curl -s http://localhost:4000/v1/models | python -m json.tool
# → one entry: "qwen-local"  (the commented cloud entries are invisible until enabled)
```

Now run a chat completion **through the proxy** (not the model directly):

```bash
curl -s http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen-local", "messages": [{"role": "user", "content": "One sentence: what is an insurance surveyor?"}]}' \
  | python -m json.tool
```

The response is OpenAI-shaped JSON. The client cannot tell — and does not care —
that a 0.5B local model answered rather than a cloud provider. That opacity is
the entire product.

## Step 2 — The one-line switch: API → proxy (8 min)

Open `docker-compose.yml` and find the api service environment:

```yaml
# THE v1 -> v2 CHANGE: one env line. Session 6 pointed this at the
# model container (http://model:8090/v1); v2 points at the proxy.
LLM_BASE_URL: http://litellm:4000/v1
```

Same protocol end to end, so **nothing in `api/app.py` changed** (see the
header comment in that file). Test the structured endpoint:

```bash
curl -s http://localhost:8000/ask \
  -H "Content-Type: application/json" -H "X-API-Key: local-dev-key" \
  -d '{"question": "What is the status of my claim?", "claim_id": "CLM-1001"}' \
  | python -m json.tool
# → {answer, claim_id: "CLM-1001", status: "Under Review", confidence: "high"}
```

And the SSE stream (watch tokens arrive):

```bash
curl -N http://localhost:8000/ask/stream \
  -H "Content-Type: application/json" -H "X-API-Key: local-dev-key" \
  -d '{"question": "Why was my claim rejected?", "claim_id": "CLM-1003"}'
```

## Step 3 — The Next.js streaming UI (12 min)

```bash
cd web
npm run dev            # starts on port 3001
```

Open **http://localhost:3001** and ask: *"What does the motor policy say about
licence validity?"* — watch the answer stream in word by word.

The chain your request travels:

```
useChat (page.tsx)  →  POST /api/chat (route.ts)  →  streamText + createOpenAI
        →  LiteLLM proxy :4000  →  model :8090  →  tokens stream back the same path
```

Three things to notice in the code:
- `web/app/api/chat/route.ts` — `createOpenAI({ baseURL: LITELLM_URL })`: the
  provider key (if any) lives **server-side only**; the browser never sees it.
- `result.toDataStreamResponse()` — wraps tokens in the AI SDK data stream
  protocol that `useChat` consumes.
- `web/app/page.tsx` — no SSE parsing anywhere; `useChat` re-renders
  `messages` as chunks land.

## Step 4 — Resilience demo (12 min)

**Local-only version (no cloud key):**

```bash
docker compose stop model     # simulate a provider outage
```

Ask another question in the chat at :3001. Watch what happens:
LiteLLM retries (`num_retries: 2`), finds no fallback deployment, and returns
a **clean error** — which the UI surfaces in a styled error box instead of
hanging or crashing. Then recover:

```bash
docker compose start model    # wait for "healthy", then chat again
```

> **CLOUD PATH (optional) — the marquee demo: automatic failover.**
> 1. In `litellm/config.yaml`, uncomment the `gpt-4o-mini` entry.
> 2. In `.env`, set `OPENAI_API_KEY=sk-...`.
> 3. `docker compose restart litellm`
> 4. Repeat the test: `docker compose stop model`, then ask a question.
>
> This time the same request **fails over to gpt-4o-mini mid-conversation** —
> the UI, the route handler and the API did not change at all; the router's
> `fallbacks: [{"qwen-local": ["gpt-4o-mini"]}]` did the work. The identical
> mechanism works provider→provider across Azure, Bedrock and Vertex — a
> fallback chain can hop clouds.

## Step 5 — Cost & routing discussion (5 min)

Read the comment block at the bottom of `litellm/config.yaml`, then discuss:

- `routing_strategy: cost-based-routing` — with several deployments behind one
  `model_name`, the router picks the cheapest healthy one per request.
- Prices come from LiteLLM's bundled price map
  (`model_prices_and_context_window.json`); self-hosted models declare
  `input_cost_per_token` / `output_cost_per_token` in `litellm_params`.
- Spend tracking & budgets: run the proxy with a database and a
  `LITELLM_MASTER_KEY`, issue virtual keys via `/key/generate` with
  `max_budget`, and read `/spend/logs` per key/team — the proxy becomes your
  LLM cost-governance layer, not just a router.

## Step 6 — Cleanup (3 min)

```bash
# stop the Next.js dev server with Ctrl+C, then:
docker compose down
# keep the hf_cache volume (model weights) for Session 8:
docker volume ls | grep hf_cache
```

Keep this directory — Session 8 adds RAG with citations on the same stack.

---

## Local → production mapping

| Today's lab | Production counterpart |
|---|---|
| LiteLLM proxy on :4000 | An LLM gateway / API-gateway tier (LiteLLM, or a managed gateway) fronting all model traffic |
| `config.yaml` fallbacks + retries | Multi-provider / multi-region resilience policy, reviewed as infrastructure-as-code |
| Commented cloud entries + `.env` keys | A secrets manager injecting provider credentials into the gateway only |
| `cost-based-routing` discussion | FinOps for LLMs: price-aware routing, budgets and per-team spend reports |
| `next dev -p 3001` | The same app on Vercel or self-hosted Node/containers behind a CDN |
| `route.ts` holding the base URL & key | Server-side secret boundary — provider keys never ship to the browser |

## Deliverables

1. Screenshot of `curl localhost:4000/v1/models` listing `qwen-local`.
2. Screenshot of the Next.js chat at :3001 mid-stream (partial answer visible).
3. Failover evidence: either the cloud-path answer arriving with the model
   container stopped, or (local-only) the clean retry-then-error in the UI.
4. A short paragraph: when is multi-provider routing worth its operational
   complexity — and when is a single provider with good error handling enough?

## Troubleshooting

- **Proxy won't start and logs mention `gpt-4o-mini`:** some LiteLLM versions
  validate fallback targets at boot. Either uncomment the `gpt-4o-mini` model
  entry (with a key) or comment out the `fallbacks:` line until you enable it.
- **`model` container unhealthy for minutes:** first start downloads the Qwen
  weights; watch `docker compose logs -f model`.
- **Port already in use:** Session 6's stack may still be running —
  `docker compose down` in that directory first.
