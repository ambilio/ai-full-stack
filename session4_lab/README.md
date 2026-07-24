# Session 4 Lab — Production Monitoring, Drift Detection & Audit Logging (Local)

**Goal (60 min):** stand up a real observability stack around the model server — Prometheus metrics, a Grafana golden-signals dashboard with an alert, an Evidently drift report, and a regulator-grade audit log with PII redaction — entirely on the local machine.

**Stack (all local Docker):**

| Component | Role | URL |
|---|---|---|
| llm (our server) | Now exposes `/metrics` and writes `logs/audit.jsonl` | localhost:8080 |
| Prometheus | Scrapes and stores metrics every 5s | localhost:9090 |
| Grafana | Dashboards + alerting over Prometheus | localhost:3000 |
| Evidently (Python) | Drift report comparing two traffic windows | HTML file |

---

## Step 0 — Prerequisites (5 min)

Docker working (Sessions 2–3). ~6 GB free RAM (model container + Prometheus + Grafana). Your Python venv from Session 1 for the drift step.

```bash
cd session4_lab
echo "API_KEY=$(openssl rand -hex 12)" > .env    # works on Linux and macOS
mkdir -p logs && chmod 777 logs                  # audit log lands here from the container
```

## Step 1 — Start the stack and see raw metrics (10 min)

```bash
docker compose up -d --build
watch -n 2 'docker ps --format "{{.Names}} {{.Status}}"'   # wait for llm (healthy)
```

Look at what Prometheus will scrape — raw metrics, plain text:

```bash
curl -s localhost:8080/metrics | grep llm_
```

You'll see `llm_requests_total` counters and `llm_request_latency_seconds_bucket` histogram buckets. **This text format IS the monitoring contract** — everything else (Prometheus, Grafana, alerts) is built on it.

Verify Prometheus sees the target: open **localhost:9090/targets** — job `llm` must show **UP**.

## Step 2 — Generate traffic and query it (10 min)

```bash
chmod +x scripts/traffic.sh
./scripts/traffic.sh 60        # ~60 requests; every 5th uses a wrong key (deliberate 401s)
```

While it runs, query in the Prometheus UI (localhost:9090 → Graph):

```promql
rate(llm_requests_total[1m])                 # traffic by endpoint & status
histogram_quantile(0.95, sum(rate(llm_request_latency_seconds_bucket[5m])) by (le))
```

Note how the 401s appear as a separate `status="401"` series — errors are just another labeled counter.

## Step 3 — Grafana dashboard + an alert (15 min)

Open **localhost:3000** (admin / admin). The dashboard **"LLM Service — Golden Signals"** is pre-provisioned with three panels: traffic (req/s), latency (p50/p95), error rate (%). Run `./scripts/traffic.sh 60` again and watch the panels move.

Add an alert on the p95 panel: panel menu → Edit → Alert → New alert rule → condition: p95 above `4` seconds for 1 minute → Save. Then create load to trip it (run two traffic scripts simultaneously in two terminals) and watch the alert fire in Alerting → Alert rules.

**Discussion checkpoint (SLO & error budget):** if our SLO is "p95 < 4s for 99.9% of a 30-day month", the error budget is ~43 minutes of breach per month. The alert you just built is the tripwire; the budget decides how much tripping is tolerable before releases stop.

## Step 4 — Read the audit log, verify redaction (10 min)

Send a request containing PII, exactly what a regulator would worry about:

```bash
KEY=$(grep API_KEY .env | cut -d= -f2)
curl -s -X POST localhost:8080/generate \
  -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
  -d '{"text": "My phone is 9876543210, email ravi.k@example.com, Aadhaar 1234 5678 9012. What is my loan status?", "max_tokens": 40}' > /dev/null

tail -1 logs/audit.jsonl | python3 -m json.tool
```

Confirm: `prompt_redacted` shows `<PHONE_REDACTED>`, `<EMAIL_REDACTED>`, `<AADHAAR_REDACTED>` — the raw PII **never reached the log file**. That is redaction **at log time**: irreversible, safest. The trade-off (vs redaction at query time, where raw logs are stored encrypted and masked on read) is that you can never recover the original — discuss which regime a bank vs an internal tool needs.

Also note what IS retained: timestamp, request id, client, model_version, latency — the fields that let you answer "what did the model say to this customer on 3 March, and which version said it?"

## Step 5 — Drift report with Evidently (15 min)

Drift = the world changing while your model stands still. In your venv:

```bash
source ~/labenv/bin/activate           # or your venv path
pip install "evidently==0.4.40" pandas numpy
python drift/drift_report.py
```

Open `drift/drift_report.html` in a browser. The report compares a baseline traffic window against a "current" window where users send longer prompts and latency crept up. Read: which columns drifted, the drift score per column, and the overall dataset verdict.

**Key point:** nothing in the code changed — the *input distribution* did. Metrics (Step 2) tell you the service is up; drift detection tells you it may be quietly becoming wrong. In production this script runs on a schedule against real logged features, and a breach opens a retraining ticket — which is Session 5's subject.

## Step 6 — Cleanup

```bash
docker compose down
```

## Mapping to production (discussion)

| Practiced locally | Production equivalent |
|---|---|
| prometheus_client in app.py | Same library — this part is identical in prod |
| Prometheus container | Managed Prometheus (Amazon/Google/Grafana Cloud) or an ops-team cluster |
| Provisioned Grafana dashboard | Dashboards as code, reviewed in git |
| Grafana UI alert | Alert rules as code → PagerDuty/Opsgenie routing |
| audit.jsonl on disk | Append-only store with retention policy (S3 object lock, WORM) + SIEM |
| Regex PII redaction | NER-based engines (Microsoft Presidio) + regex, evaluated for recall |
| drift_report.py by hand | Scheduled job on logged features; breach → alert + retraining trigger |

## Deliverables

1. Screenshot: Prometheus target UP and one PromQL query with results
2. Screenshot: Grafana golden-signals dashboard during traffic, and the alert in "Firing" state
3. The redacted `audit.jsonl` line for your PII request
4. `drift_report.html` plus 3–4 sentences: which feature drifted most, and what action a production team should take when this report turns red
