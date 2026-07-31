# Go/No-Go Recommendation — ClaimAssist model server v2
Date: 2026-07-26 · Author: Team 3 · Shadow window: 14:05–14:25 IST (39 mirrored requests)

## Evidence table

| Criterion            | v1 (prod)   | v2 (shadow) | Delta      | Threshold        | Verdict |
|----------------------|-------------|-------------|------------|------------------|---------|
| Quality (side-by-side| 25/39 acceptable | 33/39 acceptable | +8 (+20%) | no regression    | PASS |
|  trace review)       |             |             |            |                  |      |
| p95 latency          | 2.9 s       | 3.3 s       | +0.4 s (+14%) | ≤ +20%        | PASS |
| Avg tokens / reply   | 74          | 61          | −13        | informational    | —    |
| Cost per query       | ₹0.021*     | ₹0.019*     | −9%        | ≤ +10%           | PASS |
| Error rate (5xx/timeouts) | 0/39   | 0/39        | 0          | 0                | PASS |
| Guardrail violations | n/a         | n/a         | —          | 0 (added in S10) | —    |

*infra cost: VM ₹/hour ÷ observed tokens/sec, per Session 1 formula — not API spend.

## Observations from traces (Langfuse, filter tag model_version)
- v2 answers are shorter and consistently polite; 6 v1 answers rambled past the question.
- v2 latency increase tracks its longer input prefix (more input tokens) — expected, within budget.
- No refusals, no empty outputs in either version.

## Decision:  GO
Promote v2 via the Session 3 canary path (10% → 50% → 100% with eval gates),
NOT directly to 100%. Shadow evidence is necessary but not sufficient — canary
confirms behavior under real user feedback.

## Revisit condition
Re-run shadow comparison if the prompt prefix changes again, or if drift
monitoring (Session 4) flags a shift in prompt-length distribution.

## Evidence links
- Langfuse: traces 14:05–14:25, tags model_version:v1 / model_version:v2
- Traffic script log: scripts/mirror_traffic.sh run #2
