import { createDataStreamResponse } from "ai";

// Server-side only: browser talks to this route; this route talks to ClaimAssist.
const CLAIM_API_URL = process.env.CLAIM_API_URL || "http://localhost:8000";
const CLAIM_API_KEY = process.env.CLAIM_API_KEY || "local-dev-key";

export const maxDuration = 60;

export async function POST(req: Request) {
  const { messages, claim_id } = await req.json();

  const lastUser = [...(messages || [])]
    .reverse()
    .find((m: { role: string }) => m.role === "user");

  const question = lastUser?.content?.trim();
  if (!question) {
    return new Response("No user message", { status: 400 });
  }

  // Bridge ClaimAssist SSE ({token}) → AI SDK data stream that useChat expects.
  return createDataStreamResponse({
    async execute(dataStream) {
      const res = await fetch(`${CLAIM_API_URL}/ask/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": CLAIM_API_KEY,
        },
        body: JSON.stringify({
          question,
          claim_id: claim_id || null,
        }),
      });

      if (!res.ok || !res.body) {
        const text = await res.text().catch(() => "");
        throw new Error(
          `ClaimAssist /ask/stream failed (${res.status}): ${text || res.statusText}`
        );
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const raw of lines) {
          const line = raw.trim();
          if (!line.startsWith("data: ")) continue;
          const data = line.slice("data: ".length).trim();
          if (data === "[DONE]") return;

          let event: { token?: string; error?: string };
          try {
            event = JSON.parse(data);
          } catch {
            continue;
          }

          if (event.error) {
            throw new Error(event.error);
          }
          if (event.token) {
            dataStream.write(`0:${JSON.stringify(event.token)}\n`);
          }
        }
      }
    },
    onError: (err) => (err instanceof Error ? err.message : String(err)),
  });
}
