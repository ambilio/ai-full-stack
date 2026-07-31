"""Ingest the policy documents into a persistent Chroma index — clause by clause.

Session 8 introduced this index for RAG citations; Session 9 reuses it as the
backing store of the MCP tool `search_policy`. Chunking is BY CLAUSE (each
numbered clause such as "M-2.3 Licence validity: ..." becomes one chunk) so
retrieval returns precisely citable units, with metadata {doc, clause_id}.

Run once before starting the stack:

    pip install -r requirements.txt      # chromadb
    python rag/ingest.py

The index is written to rag/chroma/ and is mounted into the API container by
docker-compose.yml, where the MCP server queries it.
"""

import re
from pathlib import Path

import chromadb

BASE_DIR = Path(__file__).resolve().parent.parent
POLICY_DIR = BASE_DIR / "data" / "policies"
CHROMA_DIR = Path(__file__).resolve().parent / "chroma"
COLLECTION = "policies"

# Clause headings look like "M-2.3 Licence validity: ..." / "H-4.2 ..." /
# "P-1.4 ..." at the start of a line. Splitting on them keeps each clause,
# including its heading, as one retrievable chunk.
CLAUSE_RE = re.compile(r"(?m)^(?P<cid>[A-Z]-\d+\.\d+)\s")


def split_clauses(text: str):
    """Yield (clause_id, clause_text) for every numbered clause in a document."""
    matches = list(CLAUSE_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[m.start():end]
        # Trim trailing section headings ("## Section ...") that belong to the
        # NEXT clause's section, plus surrounding whitespace.
        chunk = re.sub(r"(?ms)^##\s.*$", "", chunk).strip()
        if chunk:
            yield m.group("cid"), chunk


def main() -> None:
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    # Recreate the collection so re-running ingest is idempotent.
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    col = client.create_collection(COLLECTION)  # default embedding function

    ids, docs, metas = [], [], []
    for md_file in sorted(POLICY_DIR.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        for clause_id, clause_text in split_clauses(text):
            ids.append(f"{md_file.stem}:{clause_id}")
            docs.append(clause_text)
            metas.append({"doc": md_file.name, "clause_id": clause_id})
        print(f"  {md_file.name}: parsed")

    col.add(ids=ids, documents=docs, metadatas=metas)
    print(f"Ingested {len(ids)} clauses from {POLICY_DIR} into {CHROMA_DIR} "
          f"(collection '{COLLECTION}').")


if __name__ == "__main__":
    main()
