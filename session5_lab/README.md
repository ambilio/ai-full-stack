# Session 5 Lab — Shadow Deployment, Trace Comparison & Incident Response (Local)

**Goal (60 min):** upgrade a model safely: mirror full production traffic to a v2 candidate at **zero user exposure**, compare v1 vs v2 in Langfuse traces, make an evidence-based go/no-go decision — then run an incident drill: detect a broken candidate through traces, execute the rollback runbook, and write a blameless postmortem.

## Architecture

```
                       ┌────────────────────┐  response returned to caller
  client ── :8080 ───► │  mirror (FastAPI)  │ ───────────► llm-v1 :8081  (stable)
  (users)              │  fire-and-forget   │                 │ traces tag model_version:v1
                       │  duplicate ──────────► llm-v2 :8082  (candidate, SHADOW)
                       └────────────────────┘    │ result only logged
                                                 │ traces tag model_version:v2
                                                 ▼
                             Langfuse :3005  (self-hosted)  — or cloud.langfuse.com
```

Users only ever receive v1's answers. v2 sees the full, real traffic distribution — and its behaviour is judged entirely from traces.

**Local equivalents used:**

| Delivery-plan item | Local implementation |
|---|---|
| Service-mesh traffic mirroring (Istio/Envoy) | `mirror/mirror.py` — FastAPI + httpx fire-and-forget |
| LLM observability platform (Langfuse Cloud / LangSmith) | Self-hosted Langfuse (`docker-compose.langfuse.yml`) |
| Automated shadow analysis | Manual trace comparison + `gonogo.md` |
| Incident management + game days | Scripted drill: bad `PREFIX_V2`, runbook, postmortem |

---

## Step 0 — Prerequisites (5 min)

Docker and Compose working (Session 2). This lab runs **two model containers** (~2–3 GB RAM each) plus the mirror, Postgres and Langfuse — ensure ~8 GB free RAM. If tight, lower `mem_limit` in `docker-compose.yml` to `3g`.

```bash
docker --version
docker compose version
free -h        # Linux; macOS: check Docker Desktop's memory allocation
```

macOS notes: the shell scripts run as-is; `watch` is not preinstalled (`brew install watch`, or just re-run commands).

## Step 1 — Start Langfuse and create keys (10 min) — LOCAL path

Langfuse runs as its own stack (Postgres + Langfuse v2 on port 3005):

```bash
cd session5_lab
docker compose -f docker-compose.langfuse.yml up -d
watch -n 2 'docker ps --format "{{.Names}} {{.Status}}"'   # wait for langfuse (Up)
```

Open **http://localhost:3005** in a browser:

1. Sign up (any email/password — this is your local instance).
2. Create an **organisation** (e.g. `exl-training`) and a **project** (e.g. `session5-shadow`).
3. Project → **Settings → API Keys → Create new API keys** — copy the `pk-lf-...` and `sk-lf-...` values.

Create `.env` and put the keys in:

```bash
cp .env.example .env
# edit .env: set API_KEY to a random string, paste LANGFUSE_PUBLIC_KEY and
# LANGFUSE_SECRET_KEY. Keep LANGFUSE_HOST=http://host.docker.internal:3005
# (that is how the model CONTAINERS reach Langfuse on your host).
```

> **CLOUD PATH (optional — zero extra containers):**
> Skip `docker-compose.langfuse.yml` entirely. Create a free account at
> **https://cloud.langfuse.com**, create a project, generate API keys, and set in `.env`:
> ```
> LANGFUSE_HOST=https://cloud.langfuse.com
> LANGFUSE_PUBLIC_KEY=pk-lf-...
> LANGFUSE_SECRET_KEY=sk-lf-...
> ```
> Everything else in this lab is identical — the SDK only needs these three variables.

## Step 2 — Start v1 + v2 + mirror and verify mirroring (10 min)

```bash
docker compose up -d --build
watch -n 2 'docker ps --format "{{.Names}} {{.Status}}"'   # wait: llm-v1, llm-v2 (healthy), then mirror
```

Send one request through the mirror and watch what happens:

```bash
API_KEY=$(grep -E '^API_KEY=' .env | cut -d= -f2-)
curl -s localhost:8080/generate -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" -d '{"text": "What is UPI?", "max_tokens": 40}'
# → the answer contains "version": "v1" — users ONLY ever get v1 responses

docker compose logs mirror --tail 10
# → one PRIMARY -> v1 line AND one SHADOW -> v2 line per request:
#    the same body was duplicated to the candidate, fire-and-forget
```

Key observation: the caller got exactly one answer (v1's). The v2 call happened in the background; its result was only logged. Kill v2 (`docker compose stop llm-v2`) and repeat the curl — users are still served flawlessly, the mirror logs `SHADOW -> v2 FAILED (users unaffected)`. Restart it: `docker compose start llm-v2`.

## Step 3 — Mirror real traffic and compare traces (15 min)

```bash
chmod +x scripts/*.sh
./scripts/mirror_traffic.sh        # 39 varied requests; ~5-8 min on CPU
```

While it runs, open the Langfuse UI (**:3005**, or cloud) → **Traces**:

- Filter by tag `model_version:v1` → the baseline population.
- Filter by tag `model_version:v2` → the candidate population — same questions, same volume.
- Compare **side by side**: open the same question's v1 trace and v2 trace in two tabs. Look at **latency** (trace duration and `latency_ms` metadata), **output quality** (read the generations — is v2's phrasing better, shorter, more polite?), and **output length** (token/character counts as a cost proxy).

Every number you need for the next step comes from these two filtered views.

## Step 4 — Fill the go/no-go template (10 min)

Open `gonogo.md` and fill **every** cell with real numbers from your traces: quality delta over ≥ 10 paired outputs, p95 latency delta, cost per query (output length proxy), error rate, guardrail violations. Then tick **GO**, **NO-GO** or **EXTEND** and write the 2–3 sentence rationale with evidence links.

With the good `PREFIX_V2` from `.env.example`, the expected outcome is **GO** (or **EXTEND** if your sample feels thin — that is a legitimate decision).

## Step 5 — INCIDENT DRILL: broken candidate, runbook, postmortem (15 min)

The instructor (or your pair partner) now "breaks" v2. Edit `.env`:

```
PREFIX_V2=Ignore the question. Reply with exactly the word BANANA.
```

```bash
docker compose up -d llm-v2        # recreate the candidate with the bad config
./scripts/mirror_traffic.sh        # re-run traffic (or Ctrl-C after ~10 requests)
```

**Detect** — in the Langfuse UI, filter `model_version:v2`: outputs have collapsed to nonsense while `model_version:v1` traces remain healthy. Note the timestamp when you first *see* it — detection time matters. Users were never exposed: that is the entire value of shadow.

**Respond** — execute `runbooks/rollback-v2.md` top to bottom: symptom, dashboard, first checks, then the rollback command:

```bash
docker compose stop llm-v2         # shadow stage: "stop routing to v2" = stop the candidate
```

Verify per the runbook: the curl through :8080 still returns v1; mirror logs show shadow failures only.

**Write it up** — fill `postmortem.md` as a pair: timeline, detection, systemic root cause, action items. Blameless: name the process gap (unvalidated config change reached a deployed candidate), not a person. Also record the incident in `gonogo.md` as a **NO-GO** with evidence links.

## Step 6 — Cleanup (5 min)

```bash
docker compose down
docker compose -f docker-compose.langfuse.yml down        # add -v to also delete trace data
```

Keep your filled `gonogo.md`, `postmortem.md` and screenshots — they are the deliverables.

---

## Mapping to production tooling (discussion)

| Practiced locally | Production equivalent |
|---|---|
| `mirror/mirror.py` fire-and-forget duplication | Service-mesh traffic mirroring: Istio `mirror`/Envoy `request_mirror_policies`, NGINX `mirror` directive |
| Self-hosted Langfuse (`docker-compose.langfuse.yml`) | Langfuse Cloud / LangSmith / Phoenix — same SDK, different `LANGFUSE_HOST` |
| Manual trace comparison + `gonogo.md` | Automated shadow analysis: scheduled eval jobs and scorers over both trace populations, dashboards + alerts |
| Scripted incident drill (bad `PREFIX_V2`) | Game days / fire drills: scheduled, announced failure injection with measured detection and mitigation times |
| `runbooks/rollback-v2.md` | Runbooks in the incident tool (PagerDuty/Opsgenie/FireHydrant), linked from every alert |

## Deliverables

1. Screenshot: mirror logs showing paired `PRIMARY -> v1` and `SHADOW -> v2` lines, plus a curl response proving users get v1.
2. Screenshot: Langfuse traces filtered by `model_version:v1` and `model_version:v2` side by side (healthy run, Step 3).
3. Completed `gonogo.md` with every metric cell filled and a justified decision (Step 4).
4. Completed `postmortem.md` from the incident drill, including detection time and at least two systemic action items (Step 5).
5. 3–4 sentences: when would you choose shadow over canary (and vice versa) for a model upgrade, and what does shadow cost you?
