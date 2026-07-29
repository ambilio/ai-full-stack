# chat_app.py — ClaimAssist v3 UI (Session 8)
#
# v3 renders the RAG product surface:
#   - citations as st.expander "chips" under each answer (clause text inside)
#   - a confidence badge coloured green / orange / red
#   - a deliberate NOT_IN_POLICY state (honesty is a feature, not an error)
#   - an "Upload document" tab calling POST /intake and showing the fields
#
# The chat tab calls the NON-streaming POST /ask, because citations and
# confidence arrive as structured JSON with the answer. /ask/stream still
# exists (curl it); streaming citations as SSE events is the lab exercise
# noted in api/app.py.
#
# Runs on the HOST:   streamlit run ui/chat_app.py    # http://localhost:8501
import json
from pathlib import Path

import requests
import streamlit as st

API_URL = "http://localhost:8000"
CLAIMS_PATH = Path(__file__).resolve().parent.parent / "data" / "claims.json"
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"

st.set_page_config(page_title="ClaimAssist", page_icon="🛡", layout="centered")

claims = json.loads(CLAIMS_PATH.read_text())
claims_by_id = {c["claim_id"]: c for c in claims}

BADGE_COLORS = {"high": "#2C7A4B", "medium": "#B8860B", "low": "#B3402A"}


def confidence_badge(level: str) -> str:
    """Small coloured pill: green (high) / orange (medium) / red (low)."""
    color = BADGE_COLORS.get(level, "#5A6478")
    return (
        f'<span style="background:{color};color:white;padding:2px 10px;'
        f'border-radius:10px;font-size:0.78em;font-weight:600;">'
        f"confidence: {level}</span>"
    )


def render_answer(payload: dict) -> None:
    """Answer + confidence badge + citation chips (+ not-found state)."""
    if payload.get("not_found"):
        # The honest state, rendered deliberately — not as an error box.
        st.markdown(payload["answer"])
        st.markdown(confidence_badge("low"), unsafe_allow_html=True)
        st.info(
            "Not found in the policy documents. ClaimAssist refuses rather "
            "than invents — in insurance, a made-up clause is a liability."
        )
        return
    st.markdown(payload["answer"])
    st.markdown(confidence_badge(payload["confidence"]), unsafe_allow_html=True)
    for cit in payload.get("citations", []):
        # The chip: clause id + source doc on the handle, clause text inside.
        with st.expander(f"📄 [{cit['clause_id']}] — {cit['doc']}"):
            st.caption(cit["snippet"])


# ---- sidebar: claim selector ---------------------------------------------------
with st.sidebar:
    st.title("ClaimAssist v3")
    st.caption("Session 8 — RAG citations + multimodal intake")
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
        "Answers are grounded in the policy documents and cite clause ids. "
        "Out-of-corpus questions return an honest 'not in policy' state."
    )

tab_chat, tab_intake = st.tabs(["💬 Chat", "📤 Upload document"])

# ---- chat tab -------------------------------------------------------------------
with tab_chat:
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant":
                render_answer(msg["payload"])
            else:
                st.markdown(msg["content"])

    if question := st.chat_input("Ask about a claim or the policy…"):
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            try:
                with st.spinner("Retrieving clauses and generating…"):
                    r = requests.post(
                        f"{API_URL}/ask",
                        json={"question": question, "claim_id": claim_id},
                        timeout=180,
                    )
                    r.raise_for_status()
                    payload = r.json()
                render_answer(payload)
            except requests.RequestException as exc:
                payload = {
                    "answer": f"ClaimAssist API unreachable at {API_URL}: {exc}",
                    "citations": [],
                    "confidence": "low",
                    "not_found": False,
                }
                st.error(payload["answer"])
        st.session_state.messages.append({"role": "assistant", "payload": payload})

# ---- intake tab -------------------------------------------------------------------
with tab_intake:
    st.subheader("Document intake")
    st.caption(
        "Upload a claim document image (try the generated samples in "
        "data/samples/). With VISION_MODEL set, a vision model reads the image "
        "via the proxy; without it, the API uses the matching .txt sidecar — "
        "the local 0.5B model is text-only."
    )
    uploaded = st.file_uploader("Claim document (PNG/JPG)", type=["png", "jpg", "jpeg"])
    if uploaded is not None:
        st.image(uploaded, caption=uploaded.name, width=380)
        if st.button("Extract fields", type="primary"):
            try:
                with st.spinner("Extracting…"):
                    r = requests.post(
                        f"{API_URL}/intake",
                        files={"file": (uploaded.name, uploaded.getvalue(),
                                        uploaded.type or "image/png")},
                        timeout=180,
                    )
                if r.status_code == 422:
                    st.warning(r.json().get("detail", "No sidecar for this file."))
                else:
                    r.raise_for_status()
                    result = r.json()
                    src = result["source"]
                    if src == "vision_model":
                        st.success(f"Extracted by vision model: {result['model']}")
                    else:
                        st.info(
                            f"Local text fallback ({result['model']} is text-only) "
                            "— same prompt, sidecar text instead of pixels."
                        )
                    st.markdown(f"**Document type:** `{result['document_type']}`")
                    st.json(result["key_fields"])
            except requests.RequestException as exc:
                st.error(f"Intake failed: {exc}")
