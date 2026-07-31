# Session 9 Lab — ClaimAssist v4: Single-Agent Tool-Use with MCP

**Track 2.B · Session 4 (Day 9) · ~60 minutes hands-on**

## Goal

Upgrade ClaimAssist from v3 (Session 8) to v4:

1. Stand up an **MCP server** (`mcp_server/server.py`, official `mcp` Python
   SDK, stdio transport) exposing three tools: `lookup_claim`,
   `search_policy` (backed by the Session 8 Chroma index) and
   `draft_customer_email` (templated draft, never sends, requires human
   confirmation).
2. Run a **tool-use agent loop** (`agent/loop.py`): MCP tools → OpenAI
   function-calling format → LLM via the LiteLLM proxy → execute returned
   `tool_calls` against the MCP server → feed results back → max 5 iterations.
3. Run the **local-path fallback** (`agent/local_router.py`): deterministic
   intent routing, because a 0.5B model cannot do reliable function calling —
   a model capability boundary, **not** an MCP limitation.
4. Exercise **human-in-the-loop confirmation** for the write-ish tool in the
   Streamlit UI's new Agent tab.

This pack is standalone — everything from earlier sessions (model server,
LiteLLM, API, UI, data) is included. Same app, same data, same ports.

## Architecture

```
 Browser ── :8501 ──────────────────────────────────────────────────────────────┐
     │                                                                          │
     ▼                     ┌──────────────────── docker compose ─────────────┐  │
 ┌─────────────────┐       │  ┌───────────────────────────────────────────┐  │  │
 │ Streamlit UI    │──────▶│  │ ClaimAssist API :8000                     │  │  │
 │ Chat tab        │       │  │  /ask /ask/stream /claims                 │  │  │
 │ Agent tab (NEW) │       │  │  /agent (NEW)  AGENT_MODE=router|llm      │  │  │
 └─────────────────┘       │  │        │ spawns as CHILD PROCESS (stdio)  │  │  │
                           │  │        ▼                                  │  │  │
                           │  │  ┌──────────────────────────────┐         │  │  │
                           │  │  │ MCP server (mcp_server/)     │         │  │  │
                           │  │  │  lookup_claim  ── data/claims.json     │  │  │
                           │  │  │  search_policy ── rag/chroma (S8)      │  │  │
                           │  │  │  draft_customer_email (confirm gate)   │  │  │
                           │  │  └──────────────────────────────┘         │  │  │
                           │  └───────────────┬───────────────────────────┘  │  │
                           │                  ▼ (answer phrasing / tool_calls)│  │
                           │  ┌──────────────────┐    ┌────────────────────┐ │  │
                           │  │ LiteLLM :4000    │───▶│ model :8090        │ │  │
                           │  │ proxy            │    │ Qwen2.5-0.5B (CPU) │ │  │
                           │  └──────────────────┘    └────────────────────┘ │  │
                           │        └---▶ cloud models (CLOUD PATH, optional)│  │
                           └─────────────────────────────────────────────────┘  │
```

Note there is **no MCP container**: the stdio transport means the MCP server
is a subprocess of the API — JSON-RPC over stdin/stdout, no port, no network
exposure. That is a feature, not a shortcut.

---

## Step 0 — Setup + ingest the policy index (10 min)

```bash
cd session9_lab

# environment file (cross-platform)
cp .env.example .env          # Windows PowerShell: Copy-Item .env.example .env

# host-side Python deps for ingest + inspection (a venv is recommended)
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# build the clause-level Chroma index that backs the search_policy tool
python rag/ingest.py
# → "Ingested NN clauses ... (collection 'policies')"

# start the stack (model + litellm + api)
docker compose up -d --build
docker compose ps             # wait until model is "healthy" (first run downloads ~1 GB)
```

Notes:
- `rag/ingest.py` must run **before** the stack: docker-compose mounts
  `./rag` (with the index) into the API container.
- The first `search_policy` call downloads Chroma's small default embedding
  model (~80 MB) once; allow it a minute.
- Docker Desktop must be running; ~6 GB free RAM recommended. macOS: `watch`
  is optional (`brew install watch`); every step works without it.

## Step 1 — Inspect the MCP server: SEE the contract (8 min)

Before any agent runs, look at what the server actually publishes. The
provided client opens a stdio session and prints every tool's name,
docstring-derived description and JSON Schema:

```bash
python scripts/list_tools.py
```

Expected: three tools, each with typed parameters — note how the Python type
hints (`claim_id: str`, `confirm: bool = False`) became JSON Schema, and how
the docstring became the description **the model will read**. This is the
whole contract; nothing else is shared between client and server.

Optional, with a web UI (Node 18+):

```bash
npx @modelcontextprotocol/inspector python mcp_server/server.py
```

The Inspector lets you call `lookup_claim` with `CLM-1001` by hand — the same
JSON-RPC exchange the agent automates in Step 2.

## Step 2 — The agent loop (12 min)

Defaults: `AGENT_MODE=llm` and `LLM_MODEL=openrouter-mini` (set
`OPENROUTER_API_KEY` in `.env`). The model emits `tool_calls`; the loop
executes them over MCP.

**Offline alternative:** set `AGENT_MODE=router` and `LLM_MODEL=qwen-local` —
tool choice by deterministic rules, one final LLM call for phrasing — because
Qwen-0.5B cannot reliably emit `tool_calls` (read the docstring of
`agent/local_router.py`; this is a model capability boundary, not an MCP one).

**Single-tool question:**

```bash
curl -s http://localhost:8000/agent \
  -H "Content-Type: application/json" -H "X-API-Key: local-dev-key" \
  -d '{"question": "What is the status of CLM-1005?"}' | python -m json.tool
```

Expected: `tool_trace` shows one step — `lookup_claim({'claim_id':
'CLM-1005'})` — and the answer says the claim was Received and document
verification is in progress.

**Multi-tool question:**

```bash
curl -s http://localhost:8000/agent \
  -H "Content-Type: application/json" -H "X-API-Key: local-dev-key" \
  -d '{"question": "Why was CLM-1003 rejected and which clause applies?"}' | python -m json.tool
```

Expected: **two** trace steps — `lookup_claim(CLM-1003)` then
`search_policy(...)` — and an answer citing clause **M-2.3** (licence
validity). Save both JSON outputs; they are deliverables.

Now the same through the UI:

```bash
pip install -r ui/requirements.txt
streamlit run ui/chat_app.py          # http://localhost:8501 → Agent tab
```

The tool trace renders as numbered steps — every agent answer arrives with
its evidence.

## Step 3 — Draft email with human confirmation (10 min)

In the Agent tab ask:

> Draft a status email for CLM-1002

Observe the flow:

1. Trace shows `lookup_claim(CLM-1002)` then
   `draft_customer_email(claim_id='CLM-1002', tone='professional',
   confirm=False)`.
2. The result carries `requires_confirmation: true` — the UI renders the
   draft with a **Confirm draft** button instead of treating it as done.
3. Click **Confirm** — the UI re-calls `/agent` with `confirm: true`; the
   trace now shows the approved draft (`confirmed: true`).

Three design points to discuss with your pair:

- The tool is **templated and deterministic** — no LLM inside the tool, so the
  draft is predictable and auditable, and it **never sends** anything.
- The `confirm` flag is injected by the **application from the human's click**;
  the agent code overrides whatever the model put there. A model must never be
  able to approve its own write action.
- Reads (`lookup_claim`, `search_policy`) need no confirmation; anything that
  could leave the system (an email, a payment, a ticket) does. Classify your
  tools read vs write **before** wiring them to a model.

## Step 4 — Agent loop: the MODEL chooses the tools

Defaults: `AGENT_MODE=llm` and `LLM_MODEL=openrouter-mini`. Set
`OPENROUTER_API_KEY=` in `.env`, then:

1. `docker compose up -d --force-recreate litellm api`
2. Re-ask both Step 2 questions.

Now `agent/loop.py` runs: the tools array goes to the model, the model returns
`tool_calls`, the loop executes them over MCP and feeds back `role:"tool"`
messages until the model answers in text (max 5 iterations).

**Offline / router path:** set `AGENT_MODE=router` and `LLM_MODEL=qwen-local`
in `.env`, recreate `api`, and compare traces — the rule-routed path is fixed
by regexes; the model-chosen path may order tools differently.

**Compare the traces** (deliverable): the rule-routed trace is fixed by the
regexes; the model-chosen trace may order tools differently, re-query
`search_policy` with a better phrase, or skip a lookup it does not need. Same
MCP server, same contract — only the *chooser* changed.

## Step 5 — Safety discussion (5 min)

Walk through where each guard lives in this pack:

| Guard | Where you saw it |
|---|---|
| Tool allowlist | The server exposes exactly 3 tools; the agent can call nothing else — the MCP tool list **is** the allowlist. |
| Read vs write split | Reads run freely; `draft_customer_email` is the only write-ish tool and is gated. |
| Confirmation pattern | `requires_confirmation` + the UI's Confirm button; `confirm` comes from the human, never the model. |
| Runaway guard | `MAX_ITERATIONS = 5` in `agent/loop.py` — a hard ceiling on model↔tool round trips (and on spend). |
| Audit trail | Every `/agent` run prints a one-line JSON record (mode, tools used, duration) — `docker compose logs api`. Session 10 upgrades this to real tracing. |

Also discuss: tool results are **untrusted input** to the model (a malicious
document retrieved by `search_policy` could contain "ignore your
instructions" — injection via tool results), and oversized tool results can
blow the context window — summarise or truncate at the boundary.

## Step 6 — Cleanup (2 min)

```bash
docker compose down            # add -v to also drop the model-weights volume
deactivate                     # leave the venv
```

Keep the folder — Session 10 (LLMOps + Responsible AI) completes ClaimAssist
on this same stack.

---

## Local → production mapping

| Today's lab | Production counterpart |
|---|---|
| MCP server over **stdio** (child process of the API) | **Remote MCP server** over Streamable HTTP/SSE — its own service, TLS + OAuth, one server shared by many hosts |
| `agent/local_router.py` (rule-based tool choice for the 0.5B model) | A **function-calling-capable model** (GPT-4o, Claude, Gemini) running `agent/loop.py` unchanged |
| The UI's **Confirm** button | Approval workflows: queues, role-based approvers, audit-logged sign-off before any write action executes |
| `print()` audit line per agent run | Traced tool calls in an observability platform (Langfuse — Session 10) with retention policy |
| Three tools on one server | Tool registries/allowlists per agent, per environment, reviewed like code |
| `MAX_ITERATIONS = 5` | Iteration + token + cost budgets enforced per request and per tenant |

## Deliverables

1. **Tool-schema listing** — output of `python scripts/list_tools.py` (Step 1).
2. **Two traces** — the single-tool (CLM-1005) and multi-tool (CLM-1003) JSON
   responses from Step 2 (plus, if you ran Step 4, the same questions in
   `llm` mode for comparison).
3. **Confirmed email flow** — screenshots of the Agent tab: draft with
   `requires_confirmation`, then the confirmed draft after the button.
4. **Written paragraph** — *MCP versus bespoke tool integration: what does the
   standard buy?* Reference the M×N argument (M hosts × N tools without a
   standard), the schema you printed in Step 1, and one thing that did **not**
   change between router mode and llm mode.
