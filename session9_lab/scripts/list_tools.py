"""Print the MCP server's tool contract — the schemas every client sees.

Opens a client session to mcp_server/server.py (spawned as a child process,
stdio transport) and prints each tool's name, description and JSON Schema.
This is the machine-readable contract that the agent loop converts to OpenAI
function-calling format, and that any other MCP host (Claude Desktop, an IDE,
another agent) would consume identically — the point of the standard.

    python scripts/list_tools.py

Alternative with a UI: `npx @modelcontextprotocol/inspector python
mcp_server/server.py` opens the official MCP Inspector in a browser.
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE_DIR = Path(__file__).resolve().parent.parent
SERVER_PARAMS = StdioServerParameters(
    command=sys.executable,
    args=[str(BASE_DIR / "mcp_server" / "server.py")],
)


async def main() -> None:
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            print(f"MCP server 'claimassist' exposes {len(tools)} tool(s)\n")
            for t in tools:
                print("=" * 72)
                print(f"tool: {t.name}")
                print(f"description:\n{(t.description or '').strip()}\n")
                print("input schema (JSON Schema):")
                print(json.dumps(t.inputSchema, indent=2))
                print()


if __name__ == "__main__":
    asyncio.run(main())
