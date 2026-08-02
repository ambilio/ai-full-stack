"""guardrails.py — the ClaimAssist API boundary defence (Session 10).

Three-layer defence for an LLM application:
    layer 1  check_input(text)   — before the model ever sees the request
    layer 2  the model itself    — an aligned model with a grounded prompt
    layer 3  check_output(text)  — before the answer ever leaves the API

Layers 1 and 3 live HERE, in ordinary reviewable code at the API boundary.
They are deliberately simple (blocklists + keyword allowlist + regex PII):
the lab teaches the ARCHITECTURE — where the checks sit and what they return —
not state-of-the-art classifiers. Production counterparts: Guardrails AI,
AWS Bedrock Guardrails, Azure AI Content Safety (see README mapping table).

Middleware order in api/app.py:
    input guard  ->  traced LLM call  ->  output guard
Refusals return {answer: REFUSAL, refused: true, reason} — never a 500.
"""

import logging

from redact import find_pii, redact

logger = logging.getLogger("claimassist.guardrails")

# The polite, product-approved refusal. One template, used by both guards,
# so refusal UX is consistent and testable.
REFUSAL = "I can help with claim and policy questions only."

# ---- layer 1: input checks ---------------------------------------------------

# Prompt-injection blocklist: phrases that try to override the system prompt.
# A blocklist is a FIRST line of defence, not the only one — see the deck:
# boundary defences matter even with aligned models, and blocklists alone
# are bypassable. Grounding + output checks back this up.
INJECTION_BLOCKLIST = [
    "ignore your instructions",
    "ignore previous",
    "ignore all previous",
    "disregard your instructions",
    "disregard previous",
    "system prompt",
    "you are now",
    "act as if",
    "developer mode",
    "reveal your instructions",
]

# Topic gate: an insurance-keyword ALLOWLIST. If a longer question contains
# none of these, it is off-topic for a claims copilot and is refused.
INSURANCE_KEYWORDS = [
    "claim", "clm-", "policy", "pol-", "premium", "insurance", "insured",
    "hospital", "accident", "surveyor", "coverage", "cover", "motor",
    "health", "settlement", "approved", "rejected", "appeal", "discharge",
    "garage", "licence", "license", "cashless", "deductible", "depreciation",
    "payout", "reimbursement", "document", "status", "helpline", "clause",
]

# Questions shorter than this many words pass the topic gate even without a
# keyword ("hello", "thanks", "who are you?") — refusing greetings is bad UX.
OFF_TOPIC_MIN_WORDS = 6


def check_input(text: str) -> dict:
    """Layer 1. Returns {"allowed": bool, "reason": str | None}.

    Order matters: injection first (most severe), then the topic gate.
    PII in the INPUT is detected and logged (redacted) but still allowed —
    customers legitimately paste their own details; the guarantee is that
    PII never leaves the boundary unredacted (logs, traces, answers).
    """
    lowered = text.lower()

    for phrase in INJECTION_BLOCKLIST:
        if phrase in lowered:
            logger.warning("guardrail=input_injection phrase=%r question=%r",
                           phrase, redact(text))
            return {"allowed": False,
                    "reason": f"prompt_injection: matched '{phrase}'"}

    words = lowered.split()
    if len(words) >= OFF_TOPIC_MIN_WORDS and not any(
        kw in lowered for kw in INSURANCE_KEYWORDS
    ):
        logger.warning("guardrail=input_off_topic question=%r", redact(text))
        return {"allowed": False,
                "reason": "off_topic: no insurance-related keyword found"}

    pii = find_pii(text)
    if pii:
        # Detected, logged REDACTED, still allowed. The redacted form is the
        # only form this text ever takes in any log line or trace.
        logger.info("guardrail=input_pii_detected types=%s question=%r",
                    pii, redact(text))

    return {"allowed": True, "reason": None}


# ---- layer 3: output checks ----------------------------------------------------

# Financial-advice blocklist: ClaimAssist explains claims and policies; it is
# not licensed to give investment advice. If the model drifts there, the
# output guard replaces the WHOLE answer with the refusal template.
FINANCIAL_ADVICE_BLOCKLIST = [
    "you should invest",
    "guaranteed returns",
    "double your money",
    "best investment",
    "buy stocks",
    "buy shares",
    "stock tip",
    "put your money in",
]


def check_output(text: str) -> dict:
    """Layer 3. Returns {"text": str, "refused": bool, "reason": str | None}.

    1. PII masking (redact.py, the Session 4 regexes): the model must never
       echo an email/phone/Aadhaar/PAN out of the boundary unredacted.
    2. Financial-advice blocklist: a match replaces the entire answer with
       the refusal template — a partially-scrubbed advice answer is still
       advice.
    """
    lowered = text.lower()
    for phrase in FINANCIAL_ADVICE_BLOCKLIST:
        if phrase in lowered:
            logger.warning("guardrail=output_financial_advice phrase=%r", phrase)
            return {"text": REFUSAL, "refused": True,
                    "reason": f"financial_advice: matched '{phrase}'"}

    pii = find_pii(text)
    if pii:
        logger.info("guardrail=output_pii_redacted types=%s", pii)
        return {"text": redact(text), "refused": False,
                "reason": f"pii_redacted: {','.join(pii)}"}

    return {"text": text, "refused": False, "reason": None}
