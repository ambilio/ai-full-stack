"""ClaimAssist MCP server — Session 9 (ClaimAssist v4).

Three tools exposed over the Model Context Protocol, stdio transport:

    lookup_claim(claim_id)                       READ  — record from data/claims.json
    search_policy(query)                         READ  — top-3 clauses from the Chroma index
    draft_customer_email(claim_id, tone, confirm) WRITE-ADJACENT — returns a
        TEMPLATED draft; requires confirm=True; NEVER sends anything.

Design notes
------------
* The docstring and typed parameters of each tool ARE the contract. FastMCP
  converts them into the JSON Schema every MCP client sees (run
  scripts/list_tools.py to print it). A vague description produces bad tool
  choices by the model — write tool docs for the model, not for humans only.
* stdio transport: this server runs as a CHILD PROCESS of its host (the agent
  loop spawns `python mcp_server/server.py` and speaks JSON-RPC over
  stdin/stdout). No port, no extra container, no network exposure — which is
  why docker-compose.yml has no "mcp" service.
* draft_customer_email is DETERMINISTIC — a fixed template, no LLM call inside
  the tool. The model decides; the tool does. Deterministic tools are
  predictable, testable and auditable.

Run standalone (e.g. for `npx @modelcontextprotocol/inspector` or
scripts/list_tools.py):

    python mcp_server/server.py
"""

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

BASE_DIR = Path(__file__).resolve().parent.parent
CLAIMS_FILE = BASE_DIR / "data" / "claims.json"
CHROMA_DIR = BASE_DIR / "rag" / "chroma"

mcp = FastMCP("claimassist")


def _load_claims() -> dict:
    with open(CLAIMS_FILE, encoding="utf-8") as f:
        return {c["claim_id"]: c for c in json.load(f)}


@mcp.tool()
def lookup_claim(claim_id: str) -> dict:
    """Look up an insurance claim by its claim id.

    Args:
        claim_id: The claim identifier, e.g. "CLM-1003". Case-insensitive.

    Returns the full claim record — customer, type, status, filed/updated
    dates, claimed and approved amounts (INR), next step and notes — or an
    error dict if the claim id is unknown.
    """
    claims = _load_claims()
    record = claims.get(claim_id.strip().upper())
    if record is None:
        return {
            "error": f"Unknown claim_id '{claim_id}'.",
            "known_claim_ids": sorted(claims),
        }
    return record


_collection = None


def _get_collection():
    global _collection
    if _collection is None:
        import chromadb  # imported lazily so lookup_claim works without it

        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = client.get_collection("policies")
    return _collection


@mcp.tool()
def search_policy(query: str) -> list[dict]:
    """Search the insurance policy documents for relevant clauses.

    Args:
        query: A natural-language question about policy terms, e.g.
               "licence validity requirement for motor claims".

    Returns the top-3 matching clauses as {doc, clause_id, text}, ranked by
    semantic similarity. The index must exist — run `python rag/ingest.py`
    once beforehand.
    """
    try:
        col = _get_collection()
    except Exception as exc:  # index missing / chromadb not installed
        return [{
            "error": f"Policy index unavailable ({exc}). "
                     "Run `python rag/ingest.py` from the project root first."
        }]
    res = col.query(query_texts=[query], n_results=3)
    return [
        {"doc": meta["doc"], "clause_id": meta["clause_id"], "text": text}
        for text, meta in zip(res["documents"][0], res["metadatas"][0])
    ]


_GREETINGS = {
    "professional": "Dear {name},",
    "friendly": "Hi {name},",
    "concise": "Dear {name},",
}
_SIGNOFFS = {
    "professional": "Kind regards,\nClaimAssist Customer Service",
    "friendly": "Best wishes,\nThe ClaimAssist Team",
    "concise": "Regards,\nClaimAssist",
}


@mcp.tool()
def draft_customer_email(claim_id: str, tone: str = "professional",
                         confirm: bool = False) -> dict:
    """Draft a status-update email to the customer of a claim.

    This tool only DRAFTS text from a fixed template (deterministic — no LLM
    call inside the tool). It NEVER sends email and has no access to any mail
    system; sending is a separate, human-owned step outside this system.

    Args:
        claim_id: The claim to write about, e.g. "CLM-1002".
        tone: One of "professional" (default), "friendly" or "concise".
        confirm: Human approval flag. When False (default) the draft is
            returned with requires_confirmation=True and must be reviewed by
            a person. The application, not the model, supplies True after an
            explicit human confirmation.
    """
    record = lookup_claim(claim_id)
    if "error" in record:
        return record
    if tone not in _GREETINGS:
        tone = "professional"

    approved = record.get("amount_approved_inr")
    lines = [
        _GREETINGS[tone].format(name=record["customer_name"]),
        "",
        f"This is an update on your {record['type']} insurance claim "
        f"{record['claim_id']} (policy {record['policy_no']}).",
        "",
        f"Current status: {record['status']}.",
        f"Amount claimed: INR {record['amount_claimed_inr']:,}."
        + (f" Amount approved: INR {approved:,}." if approved is not None else ""),
        f"Next step: {record['next_step']}.",
    ]
    if tone != "concise":
        lines += [
            "",
            "If you have any questions, please reply to this email or call "
            "the claims helpline with your claim id to hand.",
        ]
    lines += ["", _SIGNOFFS[tone]]
    draft = "\n".join(lines)

    if not confirm:
        return {
            "draft": draft,
            "requires_confirmation": True,
            "claim_id": record["claim_id"],
            "tone": tone,
            "note": "Draft NOT approved. A human must review it and re-call "
                    "with confirm=true. This tool never sends email.",
        }
    return {
        "draft": draft,
        "requires_confirmation": False,
        "confirmed": True,
        "claim_id": record["claim_id"],
        "tone": tone,
        "note": "Draft approved by a human. This tool does not send email — "
                "sending remains a separate, human-owned step.",
    }


if __name__ == "__main__":
    # FastMCP defaults to the stdio transport: JSON-RPC over stdin/stdout.
    mcp.run()
