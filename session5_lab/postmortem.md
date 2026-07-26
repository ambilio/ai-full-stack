# Blameless Postmortem — _incident title_

> **Blameless means:** the report names systems, gaps and processes — never
> people. "The deploy process allowed an unvalidated prompt change" is
> acceptable; "X pushed a bad prompt" is not. People acting reasonably on the
> information they had are never the root cause.

| | |
|---|---|
| Date of incident | |
| Severity | SEV-___ (1 = user-facing outage · 2 = degraded · 3 = no user impact) |
| Status | Draft / Reviewed / Actions closed |
| Authors (pair) | |
| Duration (detect → mitigate) | |

## Summary (2–3 sentences)

_What happened, what the user impact was (in this drill: none — v2 was shadow-only), how it was resolved._

>

## Impact

- Users affected: _in the shadow stage this should be zero — state the evidence._
- Requests affected: _how many mirrored requests hit the bad candidate._
- Cost/other impact: _e.g. wasted GPU/CPU on bad shadow inference._

## Timeline (UTC or local — state which)

| Time | Event |
|---|---|
| | Bad `PREFIX_V2` deployed to the candidate (instructor action) |
| | Traffic run started (`scripts/mirror_traffic.sh`) |
| | Anomaly first visible in Langfuse v2 traces |
| | Anomaly detected by the pair (how?) |
| | Runbook `runbooks/rollback-v2.md` executed — `docker compose stop llm-v2` |
| | Mitigation verified (users on v1, shadow stopped) |

## Detection

_How was the problem noticed? Trace comparison, mirror logs, alert? How long did detection take, and what would have made it faster?_

>

## Root cause and contributing factors

_Systemic causes only. Example: candidate configuration is a free-text env var with no eval gate between edit and deploy; shadow traces had no automated quality comparison, so detection was manual._

1.
2.

## What went well

-

## What went poorly

-

## Action items

| # | Action | Type (prevent / detect / mitigate) | Owner | Due |
|---|---|---|---|---|
| 1 | _e.g. add the failing output as a promptfoo test case (Session 3 gate)_ | prevent | | |
| 2 | _e.g. alert when v2 trace quality/latency diverges from v1_ | detect | | |
| 3 | | | | |
