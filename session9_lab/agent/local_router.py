"""Deterministic intent router — the LOCAL-path agent (AGENT_MODE=router).

WHY THIS FILE EXISTS
--------------------
Reliable function calling is a TRAINED MODEL CAPABILITY, not a protocol
feature. gpt-4o-mini and Claude emit well-formed `tool_calls` because they
were explicitly trained to; Qwen2.5-0.5B — the offline model this course runs
on CPU — was not. Handed a tools array, a 0.5B model typically produces
malformed JSON, hallucinated tool names, or prose that ignores the tools.

That is a capability boundary of the SMALL MODEL, not a limitation of MCP:
the MCP server, the tool contracts and the tool trace are IDENTICAL on both
paths. So on the local path we split the work by capability:

    * tool CHOICE   -> deterministic rules below (regex + keywords), 100%
                       reliable and free;
    * tool EXECUTION-> the same MCP client session as agent/loop.py;
    * PHRASING      -> ONE final LLM call, the thing a 0.5B model can do.

Swap in a function-calling-capable model via LiteLLM, set AGENT_MODE=llm,
and agent/loop.py replaces this file with no other change. Designing around
what the deployed model can actually do — and being honest about it in the
product — is capability-aware engineering, and it is the point of this file.
"""

import os
import re

import httpx
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from agent.loop import SERVER_PARAMS, parse_result, result_text, summarise

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:4000/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "local")
LLM_MODEL = os.getenv("LLM_MODEL", "openrouter-mini")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "60"))

# ---- intent rules -----------------------------------------------------------
CLAIM_ID_RE = re.compile(r"\bCLM[-\s]?(\d{4})\b", re.IGNORECASE)
EMAIL_KEYWORDS = ("email", "e-mail", "mail", "draft", "letter", "write to")
POLICY_KEYWORDS = ("policy", "clause", "cover", "coverage", "covered", "why",
                   "reject", "rule", "exclusion", "deduct", "cap", "waiting",
                   "appeal", "apply", "applies")


def route(question: str) -> list:
    """Map a question to an ordered tool plan: [(tool_name, args), ...].

    Rules (deliberately simple and inspectable):
      * every claim id mentioned            -> lookup_claim
      * email/draft vocabulary + a claim id -> draft_customer_email
      * policy/clause vocabulary            -> search_policy
      * nothing matched                     -> search_policy(question) fallback
    """
    q = question.lower()
    claim_ids = [f"CLM-{m.group(1)}" for m in CLAIM_ID_RE.finditer(question)]
    wants_email = any(k in q for k in EMAIL_KEYWORDS)
    wants_policy = any(k in q for k in POLICY_KEYWORDS)

    plan = [("lookup_claim", {"claim_id": cid}) for cid in dict.fromkeys(claim_ids)]

    if wants_email and claim_ids:
        tone = ("friendly" if "friendly" in q
                else "concise" if ("concise" in q or "short" in q)
                else "professional")
        plan.append(("draft_customer_email",
                     {"claim_id": claim_ids[0], "tone": tone}))
    elif wants_policy or not plan:
        plan.append(("search_policy", {"query": question}))

    return plan


# ---- the router run ----------------------------------------------------------
PHRASE_PROMPT = (
    "You are ClaimAssist, an insurance claims assistant. Below are results "
    "from internal tools, answering the customer question. Write a brief, "
    "factual, polite answer using ONLY these results. Quote clause ids like "
    "M-2.3 when present. Amounts are in INR. Do not invent anything."
)


async def _phrase(question: str, tool_notes: str) -> str:
    """ONE final LLM call: turn raw tool output into a readable answer."""
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_S) as client:
        r = await client.post(
            f"{LLM_BASE_URL}/chat/completions",
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": PHRASE_PROMPT},
                    {"role": "user",
                     "content": f"Question: {question}\n\nTool results:\n{tool_notes}"},
                ],
                "max_tokens": 220,
            },
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


async def run_router(question: str, confirm: bool = False) -> dict:
    """Rule-routed agent run. Same return shape as agent.loop.run_agent."""
    plan = route(question)
    trace: list = []
    notes: list = []

    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for name, args in plan:
                # Human-in-the-loop: confirmation comes from the API caller.
                if name == "draft_customer_email":
                    args = {**args, "confirm": bool(confirm)}
                call = await session.call_tool(name, args)
                text = result_text(call)
                trace.append({
                    "tool": name,
                    "args": args,
                    "result_summary": summarise(text),
                    "result": parse_result(text),
                })
                notes.append(f"[{name}({args})]\n{text}")

    drafted = any(t["tool"] == "draft_customer_email" for t in trace)
    if drafted:
        # The draft IS the deliverable — no LLM rewrite of a templated,
        # human-reviewed artefact. The UI renders it with a Confirm control.
        answer = ("Draft prepared and approved — see the tool trace."
                  if confirm else
                  "A draft email has been prepared. It requires human "
                  "confirmation before use — review it below and confirm.")
    else:
        try:
            answer = await _phrase(question, "\n\n".join(notes))
        except httpx.HTTPError as exc:
            answer = ("(Answer model unavailable: "
                      f"{exc}. Raw tool results:) " +
                      " | ".join(t["result_summary"] for t in trace))

    return {"answer": answer, "tool_trace": trace}
