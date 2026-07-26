# Runbook — Roll Back the v2 Model Candidate

| | |
|---|---|
| Service | llm-server (shadow stage: v1 live, v2 mirrored) |
| Owner | _name / rotation_ |
| Last tested | _date — a runbook that is never rehearsed rots_ |
| Expected time to mitigate | < 2 minutes |

## Symptom

_What a responder observes that triggers this runbook. Examples: v2 traces in
Langfuse show degraded/nonsense outputs or latency far above v1; mirror logs
show sustained SHADOW failures; v2 container unhealthy or consuming excessive
CPU/RAM._

>

## Dashboard

Where to look, in order:

1. Langfuse UI (http://localhost:3005 or https://cloud.langfuse.com) → Traces → filter tag `model_version:v2`, compare against `model_version:v1`.
2. `docker compose logs mirror --tail 50` — PRIMARY vs SHADOW lines, statuses and latencies.
3. `docker compose ps` — container health states.

## First checks (≤ 5 minutes)

1. Confirm users are unaffected: `curl -s localhost:8080/generate ...` returns a v1 answer (`"version": "v1"`).
2. Open the three most recent `model_version:v2` traces; note output text and latency.
3. Diff configuration: `grep PREFIX_V2 .env` — was the candidate's prompt/config changed recently?
4. Record findings (they feed the postmortem timeline).

## Rollback command

In the shadow stage, v2 serves **no** user traffic, so "stop routing to v2" means stopping the mirrored copy — i.e. stop the candidate container:

```bash
docker compose stop llm-v2
```

Verify:

```bash
curl -s localhost:8080/generate -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" -d '{"text": "health probe", "max_tokens": 10}'
# → still a v1 answer; mirror logs now show "SHADOW -> v2 FAILED (users unaffected)"
```

_(If v2 had already been promoted to live traffic, rollback = route traffic back to v1 — the Session 3 `rollback.sh` pattern — then stop v2.)_

## Escalation

_If the rollback does not clear the symptom within 10 minutes, or v1 is also affected:_

| Order | Contact | Channel |
|---|---|---|
| 1 | _on-call service owner_ | |
| 2 | _platform / infra on-call_ | |
| 3 | _engineering manager (SEV1 only)_ | |

## Comms

- Post in _#incident channel_: symptom, severity, "v2 mirroring stopped, users on v1, no user impact expected", link to the Langfuse evidence.
- Update every 30 minutes until the incident is closed.
- After closure: schedule the blameless postmortem (template: `postmortem.md`) within 48 hours.
