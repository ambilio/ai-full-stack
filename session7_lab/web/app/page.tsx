"use client";

import { useState } from "react";
import { useChat } from "ai/react";

const CLAIMS = [
  "CLM-1001",
  "CLM-1002",
  "CLM-1003",
  "CLM-1004",
  "CLM-1005",
  "CLM-1006",
];

export default function Chat() {
  const [claimId, setClaimId] = useState("");

  // useChat POSTs to /api/chat; claim_id is forwarded in the body so the
  // route can call ClaimAssist /ask/stream with grounding.
  const { messages, input, handleInputChange, handleSubmit, isLoading, error } =
    useChat({
      body: {
        claim_id: claimId || undefined,
      },
    });

  return (
    <main className="chat">
      <header className="bar">
        ClaimAssist <span>v2 — Next.js → /ask/stream → LiteLLM</span>
        <label
          style={{ marginLeft: "1rem", fontWeight: 400, fontSize: "0.85rem" }}
        >
          Claim{" "}
          <select
            value={claimId}
            onChange={(e) => setClaimId(e.target.value)}
            style={{ marginLeft: "0.35rem" }}
          >
            <option value="">(no claim)</option>
            {CLAIMS.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
      </header>

      <div className="messages">
        {messages.length === 0 && (
          <p className="empty">
            Select a claim, then ask e.g. &quot;What is the status of my
            claim?&quot;
          </p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            <span className="who">{m.role === "user" ? "You" : "ClaimAssist"}</span>
            {m.content}
          </div>
        ))}
        {error && (
          <div className="msg error">
            Upstream error: {error.message || "request failed"}
          </div>
        )}
      </div>

      <form className="composer" onSubmit={handleSubmit}>
        <input
          value={input}
          onChange={handleInputChange}
          placeholder="Ask about a claim…"
        />
        <button type="submit" disabled={isLoading}>
          Send
        </button>
      </form>
    </main>
  );
}
