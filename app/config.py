"""
Central place for loading configuration from environment variables.
Everything else in the app should import from here instead of
calling os.environ directly.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Project root = the folder that contains app/, knowledge_base/ and .env.
# Paths below are built from this so the app works no matter which folder
# the server or ingest script is launched from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_BASE_DIR = PROJECT_ROOT / "knowledge_base"
CHROMA_PATH = PROJECT_ROOT / "chroma_db"
STATIC_DIR = PROJECT_ROOT / "app" / "static"

load_dotenv(PROJECT_ROOT / ".env")  # reads .env from the project root if present

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# Default is only used if GEMINI_MODEL is missing from .env. Google retires
# free-tier model names often; gemini_client.py also falls back across a list.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
    )
