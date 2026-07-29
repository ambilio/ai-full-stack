// Server-side only: browser → this route → ClaimAssist /ask/stream → LiteLLM.
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
    return new Response(
      `ClaimAssist /ask/stream failed (${res.status}): ${text || res.statusText}`,
      { status: 502 }
    );
  }

  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  // Emit the AI SDK 3 data-stream protocol that useChat understands
  // (same format as toDataStreamResponse): lines like 0:"token"\n
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const reader = res.body!.getReader();
      let buffer = "";

      const writeText = (token: string) => {
        controller.enqueue(encoder.encode(`0:${JSON.stringify(token)}\n`));
      };

      try {
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
            if (data === "[DONE]") {
              controller.close();
              return;
            }

            let event: { token?: string; error?: string };
            try {
              event = JSON.parse(data);
            } catch {
              continue;
            }

            if (event.error) {
              // 3: is the AI SDK error stream part
              controller.enqueue(
                encoder.encode(`3:${JSON.stringify(event.error)}\n`)
              );
              controller.close();
              return;
            }
            if (event.token) {
              writeText(event.token);
            }
          }
        }
        controller.close();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        controller.enqueue(encoder.encode(`3:${JSON.stringify(msg)}\n`));
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "X-Vercel-AI-Data-Stream": "v1",
    },
  });
}
