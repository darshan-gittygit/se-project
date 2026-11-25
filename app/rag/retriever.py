"""
Retrieval side of the RAG pipeline. Call retrieve(query) to get the
top-k most relevant knowledge base chunks for a given piece of text
(usually the domain + a summary of the requirements).
"""
import chromadb

from app.config import CHROMA_PATH
from app.rag.embeddings import embed_text

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


def retrieve(query: str, top_k: int = 4) -> list[dict]:
    """
    Returns up to top_k chunks as [{"text": str, "source": str, "distance": float}, ...],
    sorted by relevance (lowest distance = most relevant first).
    distance is ChromaDB's default L2 (squared Euclidean) distance between the
    query and chunk embeddings - lower is better, 0 = identical. The relevance
    threshold in compliance_agent.py (0.65) was chosen empirically against
    this metric, so do not switch the collection's distance metric without
    re-tuning that threshold.
    """
    collection = _get_collection()
    query_embedding = embed_text(query)

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
    )

    chunks = []
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for doc, meta, dist in zip(documents, metadatas, distances):
        chunks.append({
            "text": doc,
            "source": meta.get("source", "unknown"),
            "distance": dist,
        })
    return chunks
