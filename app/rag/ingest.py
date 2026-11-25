"""
Builds (or rebuilds) the persistent ChromaDB knowledge base from the
curated text files in knowledge_base/.

Run this once before using the Compliance agent, and again any time you
edit or add files in knowledge_base/:

    python -m app.rag.ingest

This requires network access to Gemini's embedding API, so it must be run
on your machine (with your API key in .env), not in any sandboxed
environment without internet access.
"""
import os
import glob
import chromadb

from app.config import KNOWLEDGE_BASE_DIR, CHROMA_PATH
from app.rag.embeddings import embed_texts

COLLECTION_NAME = "compliance_kb"


def _chunk_text(text: str) -> list[str]:
    """
    Splits a document into paragraph-level chunks (blank-line separated).
    Simple on purpose - these curated docs are already written in
    self-contained paragraphs, so no need for fancy sliding-window chunking.
    """
    chunks = [p.strip() for p in text.split("\n\n") if p.strip()]
    return chunks


def build_knowledge_base():
    files = sorted(glob.glob(os.path.join(str(KNOWLEDGE_BASE_DIR), "*.txt")))
    if not files:
        raise RuntimeError(
            f"No .txt files found in {KNOWLEDGE_BASE_DIR} - nothing to ingest."
        )

    all_chunks = []
    all_ids = []
    all_metadatas = []

    for filepath in files:
        source_name = os.path.splitext(os.path.basename(filepath))[0]
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
        chunks = _chunk_text(text)
        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            all_ids.append(f"{source_name}-{i}")
            all_metadatas.append({"source": source_name})

    print(f"Found {len(files)} document(s), {len(all_chunks)} chunk(s) total. Embedding...")

    # Embed in one batch call - fine at this scale (a few dozen chunks)
    embeddings = embed_texts(all_chunks)

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    # Fresh collection each run, so re-running after editing knowledge_base/
    # doesn't leave stale chunks behind
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(COLLECTION_NAME)

    collection.add(
        ids=all_ids,
        embeddings=embeddings,
        documents=all_chunks,
        metadatas=all_metadatas,
    )

    print(f"Knowledge base built: {len(all_chunks)} chunks stored in {CHROMA_PATH}")


if __name__ == "__main__":
    build_knowledge_base()
