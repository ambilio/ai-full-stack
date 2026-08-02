# chat_app.py — ClaimAssist v5 chat UI (Session 10)
#
# v5 UI changes:
#   - REFUSAL RENDERING: refused answers show the polite refusal message plus
#     a "reason chip" — refusal is a designed product state, not an error.
#   - The sidebar shows the ACTIVE PROMPT VERSION (from GET /health), so a
#     prompt release (PROMPT_VERSION in .env) is visible in the product.
#   - Calls POST /ask (non-streaming): the output guard must see the whole
#     answer before anything reaches the user.
#
# Runs on the HOST:  streamlit run ui/chat_app.py   ->  http://localhost:8501
import json
import uuid
from pathlib import Path

import requests
import streamlit as st

API_URL = "http://localhost:8000"
API_KEY = "local-dev-key"  # matches API_KEY in .env
CLAIMS_PATH = Path(__file__).resolve().parent.parent / "data" / "claims.json"

st.set_page_config(page_title="ClaimAssist", page_icon="🛡", layout="centered")

claims = json.loads(CLAIMS_PATH.read_text())
claims_by_id = {c["claim_id"]: c for c in claims}

# One session id per browser session — sent as X-Session-Id so every question
# in this conversation groups under one session in Langfuse.
if "session_id" not in st.session_state:
    st.session_state.session_id = "ui-" + uuid.uuid4().hex[:12]

# ---- sidebar: claim selector + ops panel --------------------------------------
with st.sidebar:
    st.title("ClaimAssist v5")
    st.caption("Session 10 — LLMOps & Responsible AI. ClaimAssist is complete.")
    options = ["(no claim selected)"] + [
        f"{c['claim_id']} — {c['customer_name']}" for c in claims
    ]
    choice = st.selectbox("Claim", options)
    claim_id = choice.split(" — ")[0] if choice != "(no claim selected)" else None
    if claim_id:
        c = claims_by_id[claim_id]
        st.divider()
        st.markdown(f"**Status:** {c['status']}")
        st.markdown(f"**Type:** {c['type']}")
        st.markdown(f"**Claimed:** ₹{c['amount_claimed_inr']:,}")
        st.markdown(f"**Next step:** {c['next_step']}")
    st.divider()
    # Active prompt version — a release (PROMPT_VERSION in .env) shows up here.
    try:
        health = requests.get(f"{API_URL}/health", timeout=5).json()
        st.markdown(f"**Prompt version:** `{health.get('prompt_version', '?')}`")
        st.markdown(
            "**Tracing:** "
            + ("Langfuse ON" if health.get("langfuse_enabled") else "off (no keys)")
        )
    except requests.RequestException:
        st.warning(f"API unreachable at {API_URL}")
    st.caption(f"Session: `{st.session_state.session_id}`")

# ---- chat history --------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("refused"):
            st.caption(f"🚫 refused · reason: `{msg.get('reason')}`")


def ask_api(question: str, selected_claim: str | None) -> dict:
    r = requests.post(
        f"{API_URL}/ask",
        json={"question": question, "claim_id": selected_claim},
        headers={
            "X-API-Key": API_KEY,
            "X-Session-Id": st.session_state.session_id,
        },
        timeout=180,
    )
    r.raise_for_status()
    return r.json()


# ---- chat input -----------------------------------------------------------------
if question := st.chat_input("Ask about a claim — e.g. where is my claim?"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner("ClaimAssist is thinking…"):
                data = ask_api(question, claim_id)
            st.markdown(data["answer"])
            if data.get("refused"):
                # Refusal UX: polite message + reason chip. A refusal is a
                # product feature working, not a failure to hide.
                st.caption(f"🚫 refused · reason: `{data.get('reason')}`")
            st.session_state.messages.append({
                "role": "assistant",
                "content": data["answer"],
                "refused": data.get("refused", False),
                "reason": data.get("reason"),
            })
        except requests.RequestException as exc:
            err = f"ClaimAssist API unreachable at {API_URL}: {exc}"
            st.error(err)
            st.session_state.messages.append({"role": "assistant", "content": err})
