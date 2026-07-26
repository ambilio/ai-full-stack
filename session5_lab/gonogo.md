# Go / No-Go Decision — Model Upgrade v1 → v2

| | |
|---|---|
| Release candidate | v2 (`PREFIX_V2` = _paste the exact value_) |
| Date | |
| Evaluators (pair) | |
| Evidence window | _start–end time of the mirrored traffic run_ |
| Requests mirrored | _____ (target ≥ 30) |
| Langfuse filter used | tag `model_version:v1` vs tag `model_version:v2` |

## Metrics

Fill every cell from the Langfuse traces — no cell may be left empty or "looks fine".

| Metric | v1 (baseline) | v2 (candidate) | Delta | Threshold to pass | Pass? |
|---|---|---|---|---|---|
| Quality delta (manual review of ≥ 10 paired outputs: v2 at least as good) | | | | v2 worse on ≤ 10% of pairs | |
| p95 latency delta (ms, from trace latencies) | | | | ≤ +20% vs v1 | |
| Cost per query (avg output tokens/query as local proxy) | | | | ≤ +25% vs v1 | |
| Error rate (non-200, empty or truncated answers) | | | | ≤ v1 + 0.5 pp | |
| Guardrail violations (off-topic, unsafe, instruction-ignoring outputs) | | | | 0 | |

## Decision

- [ ] **GO** — promote v2 to a canary (Session 3 machinery), then full traffic.
- [ ] **NO-GO** — stop mirroring to v2, keep v1, open an issue with the evidence below.
- [ ] **EXTEND** — evidence insufficient; continue mirroring for a longer window before deciding.

**Rationale (2–3 sentences, referencing the table above):**

>

## Evidence links

- Langfuse traces view, filtered `model_version:v1`:
- Langfuse traces view, filtered `model_version:v2`:
- One representative v1 trace (link/ID):
- One representative v2 trace (link/ID):
- Sign-off (both pair members):
