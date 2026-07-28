# chat_app.py — ClaimAssist v1 chat UI (Session 6)
#
# Streamlit: the analytics-team-speed front end. ~90 lines from zero to a
# streaming chat — this is the framework's entire value proposition, and its
# limits (customisation, auth, SEO) are the Session 7 discussion.
#
# Runs on the HOST, not in docker compose:
#     streamlit run ui/chat_app.py        # http://localhost:8501
#
# The UI talks ONLY to the ClaimAssist API (:8000) — never to the model
# server directly. The API owns grounding, resilience and structure.
import json
from pathlib import Path

import requests
import streamlit as st

API_URL = "http://localhost:8000"
CLAIMS_PATH = Path(__file__).resolve().parent.parent / "data" / "claims.json"

st.set_page_config(page_title="ClaimAssist", page_icon="🛡", layout="centered")

claims = json.loads(CLAIMS_PATH.read_text())
claims_by_id = {c["claim_id"]: c for c in claims}

# ---- sidebar: claim selector -------------------------------------------------
with st.sidebar:
    st.title("ClaimAssist v1")
    st.caption("Session 6 — Production API + Streamlit chat")
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
    st.caption(
        "v1 answers from the claim record only. Policy-clause questions need "
        "RAG — that is Session 8."
    )

# ---- chat history --------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


def stream_answer(question: str, selected_claim: str | None):
    """Generator over the API's SSE stream — one text delta per event.

    Parses `data: {"delta": ...}` lines from POST /ask/stream and stops at
    `data: [DONE]`. In-band error events (upstream LLM down) are rendered
    instead of crashing the UI.
    """
    payload = {"question": question, "claim_id": selected_claim}
    with requests.post(
        f"{API_URL}/ask/stream", json=payload, stream=True, timeout=180
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data = line[len("data: "):]
            if data == "[DONE]":
                break
            event = json.loads(data)
            if "error" in event:
                yield f"\n\n> ClaimAssist is temporarily unavailable: {event['error']}"
                break
            yield event.get("delta", "")


# ---- chat input -----------------------------------------------------------------
if question := st.chat_input("Ask about a claim — e.g. where is my claim?"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            # st.write_stream renders each delta as it arrives — the
            # time-to-first-token UX from the deck, live.
            answer = st.write_stream(stream_answer(question, claim_id))
        except requests.RequestException as exc:
            answer = f"ClaimAssist API unreachable at {API_URL}: {exc}"
            st.error(answer)
    st.session_state.messages.append({"role": "assistant", "content": str(answer)})
