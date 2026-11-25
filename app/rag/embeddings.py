"""
Thin wrapper around Gemini's embedding endpoint.
Kept separate from gemini_client.py's generate_text since embeddings
use a different model and return vectors, not text.
"""
from google import genai
from app.config import GEMINI_API_KEY

_client = genai.Client(api_key=GEMINI_API_KEY)

# gemini-embedding-001 - current stable, GA embedding model as of this build.
# (text-embedding-004, the older default, was deprecated in Jan 2026 - if this
# name ever 404s, check https://ai.google.dev/gemini-api/docs/models for the
# current replacement, same pattern as the generation model retirements.)
EMBEDDING_MODEL = "gemini-embedding-001"


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embeds a batch of texts, returns one vector per input text (same order).
    Used both when building the knowledge base (ingest.py) and when
    embedding a query at retrieval time (retriever.py).
    """
    result = _client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
    )
    return [e.values for e in result.embeddings]


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]
