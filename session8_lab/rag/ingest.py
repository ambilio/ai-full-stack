# ingest.py — ClaimAssist v3 RAG ingestion (Session 8)
#
# Reads data/policies/*.md and splits them BY CLAUSE, not by fixed-size window.
# The policy documents were written with numbered clause headings ("M-2.3
# Licence validity:", "H-4.2 Room rent capping:", "P-1.4 Surveyor timelines:")
# precisely so that one chunk == one citable unit. When the LLM later answers
# "your claim was rejected under [M-2.3]", that citation resolves to exactly
# one chunk — which is what makes citations verifiable in a regulated domain.
#
# Chunks are embedded with chromadb's DEFAULT embedding function
# (all-MiniLM-L6-v2 exported to ONNX, ~80 MB, downloaded on first run and
# cached under ~/.cache/chroma/onnx_models) and stored in a PERSISTENT
# collection at rag/chroma/ so the API container can query the same store.
#
# The script is IDEMPOTENT: it deletes and recreates the collection on every
# run, so re-running after editing a policy document never leaves stale chunks.
#
# Run from the session8_lab directory:   python rag/ingest.py
import re
import sys
from pathlib import Path

import chromadb

LAB_ROOT = Path(__file__).resolve().parent.parent
POLICIES_DIR = LAB_ROOT / "data" / "policies"
CHROMA_DIR = LAB_ROOT / "rag" / "chroma"
COLLECTION = "policy_clauses"

# Clause headings look like "M-2.3 Licence validity: ..." at the start of a
# line: a letter prefix (M=motor, H=health, P=process), a dotted number, a
# short title, then a colon. Everything until the next clause heading or
# section heading ("## Section ...") belongs to that clause.
CLAUSE_RE = re.compile(r"^(?P<id>[MHP]-\d+\.\d+)\s+(?P<title>[^:\n]+):", re.MULTILINE)


def split_by_clause(text: str, doc_name: str) -> list[dict]:
    """One chunk per numbered clause. Fixed-size windows would cut clauses in
    half and merge neighbours — and a citation like [M-2.3] would then map to
    'somewhere in chunk 7' instead of one precise, quotable clause."""
    chunks = []
    matches = list(CLAUSE_RE.finditer(text))
    for i, m in enumerate(matches):
        start = m.start()
        # clause body runs to the next clause heading or the next "## Section"
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        body = re.split(r"^## ", body, maxsplit=1, flags=re.MULTILINE)[0]
        chunk_text = " ".join(body.split())  # collapse newlines/indentation
        chunks.append(
            {
                "id": m.group("id"),                       # e.g. "M-2.3"
                "title": m.group("title").strip(),          # e.g. "Licence validity"
                "text": chunk_text,                          # heading + full clause body
                "doc": doc_name,                             # e.g. "motor_policy.md"
            }
        )
    return chunks


def main() -> None:
    policy_files = sorted(POLICIES_DIR.glob("*.md"))
    if not policy_files:
        sys.exit(f"No policy documents found in {POLICIES_DIR}")

    all_chunks: list[dict] = []
    for path in policy_files:
        chunks = split_by_clause(path.read_text(encoding="utf-8"), path.name)
        print(f"{path.name}: {len(chunks)} clauses")
        all_chunks.extend(chunks)

    # PersistentClient writes the index to disk — the API process opens the
    # same directory read/write and queries it. In production this directory
    # becomes a vector database service (pgvector, a managed vector DB, ...).
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # Idempotent: drop and rebuild. Never append blindly — stale chunks from a
    # previous version of a document are a silent correctness bug in RAG.
    try:
        client.delete_collection(COLLECTION)
        print(f"Deleted existing collection '{COLLECTION}' (idempotent rebuild)")
    except Exception:
        pass  # first run: nothing to delete

    # No embedding_function argument => chromadb's default embedding function
    # (ONNX all-MiniLM-L6-v2). First run downloads ~80 MB to ~/.cache/chroma.
    collection = client.create_collection(COLLECTION, metadata={"hnsw:space": "l2"})

    collection.add(
        ids=[c["id"] for c in all_chunks],                 # clause id IS the chunk id
        documents=[c["text"] for c in all_chunks],
        metadatas=[{"doc": c["doc"], "clause_id": c["id"]} for c in all_chunks],
    )

    print(f"\nStored {collection.count()} clause chunks in {CHROMA_DIR}/")
    print("Sample chunks (id · doc · first 90 chars):")
    for c in all_chunks[:5]:
        print(f"  {c['id']:>6} · {c['doc']:<20} · {c['text'][:90]}...")


if __name__ == "__main__":
    main()
