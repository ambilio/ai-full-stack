# chat_app.py — ClaimAssist v4 chat UI (Session 9)
#
# Two tabs:
#   Chat  — the v1 streaming chat against POST /ask/stream (unchanged pattern)
#   Agent — NEW in v4: asks POST /agent, renders the answer AND the tool trace
#           as numbered steps; a draft email is shown with an explicit Confirm
#           button (human-in-the-loop for write actions).
#
# Runs on the HOST, not in docker compose:
#     streamlit run ui/chat_app.py        # http://localhost:8501
#
# The UI talks ONLY to the ClaimAssist API (:8000) — never to the model server
# or the MCP server directly. The API owns the agent loop and the MCP session.
import json
import os
from pathlib import Path

import requests
import streamlit as st

API_URL = os.getenv("CLAIMASSIST_API_URL", "http://localhost:8000")
API_KEY = os.getenv("CLAIMASSIST_API_KEY", "local-dev-key")
HEADERS = {"X-API-Key": API_KEY}
CLAIMS_PATH = Path(__file__).resolve().parent.parent / "data" / "claims.json"

st.set_page_config(page_title="ClaimAssist", page_icon="🛡", layout="centered")

claims = json.loads(CLAIMS_PATH.read_text())
claims_by_id = {c["claim_id"]: c for c in claims}

# ---- sidebar: claim selector -------------------------------------------------
with st.sidebar:
    st.title("ClaimAssist v4")
    st.caption("Session 9 — Single-agent tool-use with MCP")
    options = ["(no claim selected)"] + [
        f"{c['claim_id']} — {c['customer_name']}" for c in claims
    ]
    choice = st.selectbox("Claim (Chat tab only)", options)
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
        "The Agent tab needs no claim selector — the agent finds the claim "
        "id in your question and picks its own tools."
    )

tab_chat, tab_agent = st.tabs(["Chat", "Agent"])

# =============================== CHAT TAB =====================================
with tab_chat:
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    def stream_answer(question: str, selected_claim: str | None):
        """Generator over the API's SSE stream — one text delta per event."""
        payload = {"question": question, "claim_id": selected_claim}
        with requests.post(
            f"{API_URL}/ask/stream", json=payload, headers=HEADERS,
            stream=True, timeout=180,
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
                yield event.get("token", "")

    if question := st.chat_input("Ask about a claim — e.g. where is my claim?"):
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            try:
                answer = st.write_stream(stream_answer(question, claim_id))
            except requests.RequestException as exc:
                answer = f"ClaimAssist API unreachable at {API_URL}: {exc}"
                st.error(answer)
        st.session_state.messages.append({"role": "assistant", "content": str(answer)})

# =============================== AGENT TAB ====================================
with tab_agent:
    st.markdown(
        "Ask a question; the agent selects and runs MCP tools "
        "(`lookup_claim`, `search_policy`, `draft_customer_email`) and shows "
        "you exactly what it did."
    )

    def call_agent(question: str, confirm: bool = False) -> dict | None:
        try:
            r = requests.post(
                f"{API_URL}/agent",
                json={"question": question, "confirm": confirm},
                headers=HEADERS,
                timeout=300,
            )
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            st.error(f"Agent call failed: {exc}")
            return None

    agent_q = st.text_input(
        "Question",
        key="agent_question_input",
        placeholder="e.g. Why was CLM-1003 rejected and which clause applies?",
    )
    if st.button("Run agent", type="primary", key="run_agent_btn") and agent_q.strip():
        with st.spinner("Agent running — executing tools via MCP…"):
            result = call_agent(agent_q)
        if result is not None:
            st.session_state["agent_result"] = result
            st.session_state["agent_last_question"] = agent_q

    result = st.session_state.get("agent_result")
    if result:
        st.markdown("#### Answer")
        st.markdown(result["answer"] or "_(no answer)_")

        st.markdown(f"#### Tool trace — mode `{result.get('agent_mode', '?')}`")
        pending_draft = None
        confirmed_draft = None
        for i, step in enumerate(result["tool_trace"], start=1):
            args = ", ".join(f"{k}={v!r}" for k, v in step["args"].items())
            st.markdown(f"**{i}. `{step['tool']}({args})`**")
            st.code(step["result_summary"], language="json")
            r = step.get("result")
            if isinstance(r, dict) and "draft" in r:
                if r.get("requires_confirmation"):
                    pending_draft = r
                elif r.get("confirmed"):
                    confirmed_draft = r

        # ---- human-in-the-loop for the write-ish tool -----------------------
        if pending_draft:
            st.warning(
                "This draft **requires human confirmation**. The agent cannot "
                "approve write actions — review the draft, then confirm."
            )
            st.text_area(
                "Draft email (pending approval)",
                pending_draft["draft"],
                height=260,
                key="pending_draft_area",
            )
            if st.button("Confirm draft", type="primary", key="confirm_draft_btn"):
                with st.spinner("Re-running with confirm=true…"):
                    confirmed = call_agent(
                        st.session_state["agent_last_question"], confirm=True
                    )
                if confirmed is not None:
                    st.session_state["agent_result"] = confirmed
                    st.rerun()
        elif confirmed_draft:
            st.success(
                "Draft confirmed by a human. Note the tool never sends email — "
                "sending stays a separate, human-owned step."
            )
            st.text_area(
                "Approved draft",
                confirmed_draft["draft"],
                height=260,
                key="approved_draft_area",
            )
