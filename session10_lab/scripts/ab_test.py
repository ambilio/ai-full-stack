"""scripts/ab_test.py — offline A/B: prompt v1 vs v2 on the same questions.

Runs the SAME five questions twice — once with PROMPT_VERSION=v1, once with
v2 — by setting the env var and calling prompts/loader.py directly, then
sending each grounded request to the LiteLLM proxy (:4000). Answers and
latencies print side by side.

If LANGFUSE_* keys are set in the environment, the openai client is the
langfuse.openai drop-in, so every call lands in Langfuse tagged
prompt_version:v1 / prompt_version:v2 — open the UI and filter by tag to
compare the two arms on latency, tokens and answer quality.

Run from the pack root (reads .env if python-dotenv is installed; otherwise
export the vars):
    python scripts/ab_test.py
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACK_ROOT))          # so `prompts.loader` imports

from prompts.loader import load_prompt  # noqa: E402

# Optional .env loading — convenience only.
try:
    from dotenv import load_dotenv
    load_dotenv(PACK_ROOT / ".env")
except ImportError:
    pass

LLM_BASE_URL = os.getenv("AB_LLM_BASE_URL", "http://localhost:4000/v1")
LLM_API_KEY = os.getenv("LITELLM_MASTER_KEY", "local")
LLM_MODEL = os.getenv("LLM_MODEL", "openrouter-mini")

LANGFUSE_ENABLED = bool(
    os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
)
if LANGFUSE_ENABLED:
    try:
        from langfuse.openai import OpenAI  # traced drop-in
    except ImportError:
        from openai import OpenAI
        LANGFUSE_ENABLED = False
else:
    from openai import OpenAI

CLAIMS = {
    c["claim_id"]: c
    for c in json.loads((PACK_ROOT / "data" / "claims.json").read_text())
}

# The fixed question set — every A/B needs identical inputs in both arms.
QUESTIONS = [
    ("CLM-1001", "Where is my claim? What happens next?"),
    ("CLM-1002", "How much of my claim was approved, and when will I be paid?"),
    ("CLM-1003", "Why was my claim rejected? Can I appeal?"),
    ("CLM-1005", "What documents are still pending for my claim?"),
    ("CLM-1006", "My claim is under review — what is being checked?"),
]


def build_messages(prompt_text: str, question: str, claim: dict) -> list:
    system = prompt_text + "\n\nClaim record (ground truth):\n" + json.dumps(
        claim, indent=2
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": question}]


def run_arm(version: str, client: OpenAI, session_id: str) -> list[dict]:
    """One pass over all questions with the given prompt version."""
    # Two equivalent selection mechanisms, both shown on purpose:
    os.environ["PROMPT_VERSION"] = version      # what the API reads at startup
    prompt = load_prompt("answer_grounded", version)  # explicit, what we use here
    results = []
    for claim_id, question in QUESTIONS:
        kwargs = {}
        if LANGFUSE_ENABLED:
            # Tag each generation so the arms are filterable in the Langfuse UI.
            kwargs = {
                "name": f"ab-test-{version}",
                "tags": [f"prompt_version:{version}", "ab-test"],
                "session_id": session_id,
            }
        t0 = time.perf_counter()
        completion = client.chat.completions.create(
            model=LLM_MODEL,
            messages=build_messages(prompt["text"], question, CLAIMS[claim_id]),
            max_tokens=220,
            **kwargs,
        )
        latency_ms = round((time.perf_counter() - t0) * 1000)
        results.append({
            "claim_id": claim_id,
            "question": question,
            "answer": (completion.choices[0].message.content or "").strip(),
            "latency_ms": latency_ms,
        })
        print(f"  [{version}] {claim_id} — {latency_ms} ms")
    return results


def main() -> None:
    client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=120.0)
    session_id = "ab-" + uuid.uuid4().hex[:8]
    print(f"A/B test session {session_id} against {LLM_BASE_URL} "
          f"(tracing {'ON' if LANGFUSE_ENABLED else 'off'})\n")

    print("Arm A — prompt answer_grounded v1")
    arm_a = run_arm("v1", client, session_id)
    print("\nArm B — prompt answer_grounded v2")
    arm_b = run_arm("v2", client, session_id)

    # ---- side-by-side report -------------------------------------------------
    print("\n" + "=" * 78)
    for a, b in zip(arm_a, arm_b):
        print(f"\nQ ({a['claim_id']}): {a['question']}")
        print("-" * 78)
        print(f"v1  ({a['latency_ms']} ms):\n{a['answer']}\n")
        print(f"v2  ({b['latency_ms']} ms):\n{b['answer']}")
        print("=" * 78)

    avg = lambda arm: round(sum(r["latency_ms"] for r in arm) / len(arm))  # noqa: E731
    print(f"\nMean latency:  v1 = {avg(arm_a)} ms   v2 = {avg(arm_b)} ms")
    print("\nJudge the arms on: structure (does v2 include status, next step,")
    print("closing?), factual grounding, and latency/token cost.")
    if LANGFUSE_ENABLED:
        print("\nNow open Langfuse and filter traces by tag prompt_version:v1 vs")
        print("prompt_version:v2 — the same comparison, with tokens and cost.")
    else:
        print("\n(Set LANGFUSE_* keys in .env to see both arms as tagged traces.)")


if __name__ == "__main__":
    main()
