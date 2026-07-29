"use client";

import { useChat } from "ai/react";

export default function Chat() {
  // useChat manages the message list, the input state and the streaming
  // request to /api/chat. Tokens arrive over the AI SDK data stream protocol
  // and `messages` re-renders as each chunk lands — no manual SSE parsing.
  const { messages, input, handleInputChange, handleSubmit, isLoading, error } =
    useChat();

  return (
    <main className="chat">
      <header className="bar">
        ClaimAssist <span>v2 — Next.js → LiteLLM → model</span>
      </header>

      <div className="messages">
        {messages.length === 0 && (
          <p className="empty">
            Ask a claims question — for example: &quot;What does the motor
            policy say about licence validity?&quot;
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
            Upstream error: {error.message || "request failed"}. LiteLLM
            retried and no provider answered — is the model container running?
          </div>
        )}
      </div>

      <form className="composer" onSubmit={handleSubmit}>
        <input
          value={input}
          onChange={handleInputChange}
          placeholder="Ask about a claim or policy…"
        />
        <button type="submit" disabled={isLoading}>
          Send
        </button>
      </form>
    </main>
  );
}
