"""ClaimAssist agent loop — LLM-driven tool use over MCP (Session 9).

The loop (AGENT_MODE=llm):

    1. Spawn the MCP server as a child process and open a client session
       over stdio (mcp.client.stdio).
    2. Convert the MCP tool list into OpenAI function-calling format.
    3. Send the conversation + tools to the LLM via the LiteLLM proxy (:4000).
    4. If the model returns tool_calls: execute each against the MCP server,
       append the results as role:"tool" messages, and go to 3.
    5. If the model returns plain text, that is the final answer.
       Hard stop after MAX_ITERATIONS (5) — the runaway guard.

Returns {"answer": str, "tool_trace": [{tool, args, result_summary, result}]}.

This file works unchanged with any function-calling-capable model behind
LiteLLM (openrouter-mini, Claude, Gemini, ...) — the CLOUD path. The local
Qwen2.5-0.5B cannot reliably emit tool_calls, which is why the LOCAL path
uses agent/local_router.py instead (see its docstring for the full argument).

Safety property worth teaching: the `confirm` flag on draft_customer_email is
OVERWRITTEN with the value the human supplied to POST /agent. The model can
request a draft, but it can never approve one — write actions require a human.
"""

import json
import os
import sys
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE_DIR = Path(__file__).resolve().parent.parent

# The MCP server is a subprocess of THIS process — stdio transport.
SERVER_PARAMS = StdioServerParameters(
    command=sys.executable,
    args=[str(BASE_DIR / "mcp_server" / "server.py")],
)

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:4000/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "local")
LLM_MODEL = os.getenv("LLM_MODEL", "openrouter-mini")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "60"))

MAX_ITERATIONS = 5  # runaway guard: a hard ceiling on model<->tool round trips

SYSTEM_PROMPT = (
    "You are ClaimAssist, an insurance claims assistant. Use the available "
    "tools to look up claims, search policy clauses and draft customer "
    "emails. Base every statement on tool results — never invent claim data "
    "or clause numbers. When you have enough information, answer concisely. "
    "Amounts are in INR."
)


def mcp_tools_to_openai(mcp_tools) -> list:
    """Convert MCP tool descriptors to the OpenAI function-calling format.

    MCP already publishes name + description + JSON Schema for each tool, so
    the conversion is a re-labelling, not a translation — one reason the two
    ecosystems compose so cleanly.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in mcp_tools
    ]


def result_text(call_result) -> str:
    """Flatten an MCP CallToolResult into plain text for the LLM."""
    parts = [c.text for c in call_result.content if getattr(c, "text", None)]
    return "\n".join(parts) if parts else "(empty result)"


def summarise(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_result(text: str):
    """Best-effort JSON parse so the UI can inspect structured tool output."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


async def _chat(client: httpx.AsyncClient, messages: list, tools: list) -> dict:
    """One /chat/completions call WITH the tools array attached."""
    r = await client.post(
        f"{LLM_BASE_URL}/chat/completions",
        json={
            "model": LLM_MODEL,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": 400,
        },
        headers={"Authorization": f"Bearer {LLM_API_KEY}"},
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


async def run_agent(question: str, confirm: bool = False) -> dict:
    """The tool-use loop. Returns {"answer", "tool_trace"}."""
    trace: list = []
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = mcp_tools_to_openai((await session.list_tools()).tools)

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ]

            async with httpx.AsyncClient(timeout=LLM_TIMEOUT_S) as client:
                for _ in range(MAX_ITERATIONS):
                    msg = await _chat(client, messages, tools)
                    tool_calls = msg.get("tool_calls") or []

                    if not tool_calls:  # plain text -> final answer
                        return {
                            "answer": (msg.get("content") or "").strip(),
                            "tool_trace": trace,
                        }

                    # Echo the assistant turn, then execute every tool call.
                    messages.append({
                        "role": "assistant",
                        "content": msg.get("content"),
                        "tool_calls": tool_calls,
                    })
                    for tc in tool_calls:
                        name = tc["function"]["name"]
                        try:
                            args = json.loads(tc["function"]["arguments"] or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        # Human-in-the-loop: confirmation comes from the API
                        # caller, NEVER from the model.
                        if name == "draft_customer_email":
                            args["confirm"] = bool(confirm)

                        call = await session.call_tool(name, args)
                        text = result_text(call)
                        trace.append({
                            "tool": name,
                            "args": args,
                            "result_summary": summarise(text),
                            "result": parse_result(text),
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": text,
                        })

    return {
        "answer": "The agent hit the 5-iteration ceiling without producing a "
                  "final answer. Partial work is in the tool trace.",
        "tool_trace": trace,
    }
