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
# SQLite database for pipeline runs, approvals and the audit log. Created on
# first use; gitignored. REQUORA_DB_PATH overrides it (the tests use a temp file).
DB_PATH = Path(os.environ.get("REQUORA_DB_PATH", str(PROJECT_ROOT / "data" / "app.db")))

# Recorded in every audit event and run. Bump it when behaviour changes.
APP_VERSION = "0.5.1"


def retention_days() -> int | None:
    """RETENTION_DAYS: delete stored runs older than this many days (python -m app.retention). Unset = keep."""
    raw = os.environ.get("RETENTION_DAYS", "").strip()
    if not raw:
        return None
    days = int(raw)
    if days < 1:
        raise ValueError("RETENTION_DAYS must be a positive number of days")
    return days


def require_role() -> bool:
    """REQUIRE_ROLE=1/true/yes/on: API requests without an X-Role header get 400. Off by default (the UI sends none yet)."""
    return os.environ.get("REQUIRE_ROLE", "").strip().lower() in ("1", "true", "yes", "on")

load_dotenv(PROJECT_ROOT / ".env")  # reads .env from the project root if present

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# Default is only used if GEMINI_MODEL is missing from .env. Google retires
# free-tier model names often; gemini_client.py also falls back across a list.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
    )
