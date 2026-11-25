"""
Shared test setup. Everything here runs OFFLINE: Gemini calls and vector
retrieval are mocked, so no API key, network or chroma_db is needed.
"""
import os
import sys
from pathlib import Path

# config.py refuses to import without a key; a dummy is enough because no
# real request is ever sent during tests.
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-real")
os.environ.setdefault("GEMINI_MODEL", "test-model")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
