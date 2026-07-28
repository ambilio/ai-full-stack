import { streamText } from "ai";
import { createOpenAI } from "@ai-sdk/openai";

// Any OpenAI-compatible endpoint fits here. We point the SDK at the LiteLLM
// proxy: locally it routes to the Qwen container; with a cloud entry
// uncommented in litellm/config.yaml it fails over to that provider — this
// file never changes. The key stays on the server: the browser only ever
// talks to THIS route, never to a provider.
const litellm = createOpenAI({
  baseURL: process.env.LITELLM_URL || "http://localhost:4000/v1",
  apiKey: process.env.LITELLM_API_KEY || "local",
});

export const maxDuration = 60; // allow slow CPU generations

export async function POST(req: Request) {
  const { messages } = await req.json();

  const result = await streamText({
    model: litellm(process.env.LLM_MODEL || "qwen-local"),
    system:
      "You are ClaimAssist, an insurance claims-status assistant. " +
      "Answer briefly, factually and politely.",
    messages,
  });

  // Wraps the token stream in the AI SDK data stream protocol,
  // which useChat on the client consumes.
  return result.toDataStreamResponse();
}
