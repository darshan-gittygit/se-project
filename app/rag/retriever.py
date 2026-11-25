"""
Retrieval side of the RAG pipeline.

retrieve_many(queries) embeds a batch of short queries (one per requirement,
see compliance_agent.py) in one embedding call and returns the top-k chunks
for each. retrieve(query) is the single-query convenience form.

Distances are ChromaDB's default "l2" metric, which is the SQUARED Euclidean
distance between embeddings - lower is better, 0 = identical. Gemini
embeddings are unit length, so d = 2 - 2*cos(angle). The relevance threshold
in compliance_agent.py was tuned against this metric with
scripts/tune_relevance_threshold.py, so re-run that script if the metric, the
embedding model or the query format changes.
"""
import chromadb

from app.config import CHROMA_PATH, KNOWLEDGE_BASE_DIR
from app.rag.embeddings import embed_texts

COLLECTION_NAME = "compliance_kb"

_collection = None


def _get_collection():
    global _collection
    if _collection is None:
        if not CHROMA_PATH.is_dir():
            raise RuntimeError(
                f"Knowledge base not found at {CHROMA_PATH}. "
                "Run 'python -m app.rag.ingest' once to build it first."
            )
        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        _collection = client.get_collection(COLLECTION_NAME)
    return _collection


def known_sources() -> list[str]:
    """Source names of every knowledge-base document (file names without .txt)."""
    return sorted(p.stem for p in KNOWLEDGE_BASE_DIR.glob("*.txt"))


def retrieve_many(queries: list[str], top_k: int = 3) -> list[list[dict]]:
    """
    For each query, returns up to top_k chunks as
    [{"id": str, "text": str, "source": str, "distance": float}, ...],
    most relevant (lowest distance) first. Output order matches `queries`.
    """
    if not queries:
        return []
    collection = _get_collection()
    results = collection.query(
        query_embeddings=embed_texts(queries),
        n_results=top_k,
    )
    per_query = []
    for ids, docs, metas, dists in zip(
        results["ids"], results["documents"], results["metadatas"], results["distances"]
    ):
        per_query.append([
            {"id": cid, "text": doc, "source": (meta or {}).get("source", "unknown"), "distance": dist}
            for cid, doc, meta, dist in zip(ids, docs, metas, dists)
        ])
    return per_query


def retrieve(query: str, top_k: int = 4) -> list[dict]:
    """Single-query form of retrieve_many."""
    return retrieve_many([query], top_k)[0]
