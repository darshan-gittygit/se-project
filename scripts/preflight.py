"""
Pre-demo checks: is everything in place before the server starts?

    python -m scripts.preflight                      # local checks only, no model calls
    python -m scripts.preflight --run 6645cbcfd2d9   # also: that stored run loads
    python -m scripts.preflight --ping-model         # also: one tiny generate_text call (uses quota)
    python -m scripts.preflight --ping-embed         # also: one embed_texts call (uses quota)

Prints one PASS or FAIL line per check and exits with 1 if any check failed.
The API key is never printed, only whether it is set.

Checks: .env and the key (shows GEMINI_MODEL and REQUIRE_ROLE), the ChromaDB
collection and its chunk count, data/app.db (tables and the audit hash
chain), the static UI files and the recorded sample, and port 8001 (free, or
already serving this app). No rows are written: the tables are read through a
read-only connection and the audit chain is only recomputed.
"""
import argparse
import json
import os
import socket
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ENV_PATH = ROOT / ".env"
PLACEHOLDER_KEYS = ("", "your-key-here")   # .env.example's placeholder counts as "not set"
COLLECTION = "compliance_kb"
TABLES = ("runs", "approval_items", "decisions", "audit_events")
STATIC_FILES = ("index.html", "app.js", "style.css", "charts.js", "sample_run.json")
DEFAULT_PORT = 8001
PING_PROMPT = "Reply with the single word OK."


class CheckFailed(Exception):
    """A check did not pass; the message says why and what to do."""


def flag_on(value: str | None) -> bool:
    """Same rule as app.config.require_role (tests keep the two in step)."""
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _setting(name: str, file_values: dict, environ) -> str | None:
    # The app reads os.environ after load_dotenv, which never overrides a variable
    # already set in the shell; so the shell value wins over .env.
    return environ.get(name) if environ.get(name) is not None else file_values.get(name)


def check_env(env_path: Path | None = None, environ=None) -> str:
    env_path = Path(env_path or ENV_PATH)
    environ = os.environ if environ is None else environ
    if not env_path.is_file():
        raise CheckFailed(f"{env_path.name} not found - copy .env.example to .env and put your Gemini key in it")
    values = dotenv_values(env_path)
    key = (_setting("GEMINI_API_KEY", values, environ) or "").strip()
    if key in PLACEHOLDER_KEYS:
        raise CheckFailed("GEMINI_API_KEY is not set (empty or still the .env.example placeholder)")
    model = _setting("GEMINI_MODEL", values, environ) or "unset (the app's built-in default)"
    role = _setting("REQUIRE_ROLE", values, environ)
    role_text = f"{role} (on)" if flag_on(role) else (f"{role} (off)" if role else "unset (off)")
    return f"GEMINI_API_KEY is set; GEMINI_MODEL={model}; REQUIRE_ROLE={role_text}"


def expected_chunks(kb_dir: Path) -> tuple[int, int]:
    """(chunks, files) that python -m app.rag.ingest would store for knowledge_base/."""
    from app.rag.ingest import _chunk_text
    files = sorted(kb_dir.glob("*.txt"))
    return sum(len(_chunk_text(f.read_text(encoding="utf-8"))) for f in files), len(files)


def check_kb(chroma_path: Path | None = None, kb_dir: Path | None = None) -> str:
    import chromadb
    from app import config
    chroma_path = Path(chroma_path or config.CHROMA_PATH)
    kb_dir = Path(kb_dir or config.KNOWLEDGE_BASE_DIR)
    if not chroma_path.is_dir():
        raise CheckFailed(f"{chroma_path.name}/ not found - run python -m app.rag.ingest")
    want, n_files = expected_chunks(kb_dir)
    try:
        count = chromadb.PersistentClient(path=str(chroma_path)).get_collection(COLLECTION).count()
    except Exception as e:
        raise CheckFailed(f"collection {COLLECTION} not found in {chroma_path.name}/ ({e}) - run python -m app.rag.ingest")
    if count != want:
        raise CheckFailed(f"{COLLECTION} has {count} chunks but the {n_files} knowledge-base files give {want} - "
                          "re-run python -m app.rag.ingest")
    return f"{COLLECTION} has {count} chunks (matches the {n_files} knowledge-base files)"


def check_db() -> str:
    from app import audit, config
    path = Path(config.DB_PATH)
    if not path.is_file():
        raise CheckFailed(f"{path} not found - the server creates it on the first request; start it once")
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)   # read-only: preflight never writes
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            missing = [t for t in TABLES if t not in names]
            if missing:
                raise CheckFailed(f"{path.name} is missing table(s): {', '.join(missing)}")
            n_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise CheckFailed(f"{path.name} does not open as a SQLite database ({e})")
    chain = audit.verify_chain()
    if not chain["ok"]:
        raise CheckFailed(f"audit chain broken at seq {chain['first_bad_seq']}: {chain['reason']}")
    return f"{path.name} opens; {n_runs} run(s); audit chain ok ({chain['checked']} events)"


def check_static(static_dir: Path | None = None) -> str:
    from app import config
    static_dir = Path(static_dir or config.STATIC_DIR)
    missing = [f for f in STATIC_FILES if not (static_dir / f).is_file()]
    if missing:
        raise CheckFailed(f"missing in app/static: {', '.join(missing)}")
    try:
        sample = json.loads((static_dir / "sample_run.json").read_text(encoding="utf-8"))
    except ValueError as e:
        raise CheckFailed(f"sample_run.json does not parse ({e})")
    if not isinstance(sample, dict) or not isinstance(sample.get("requirements"), dict):
        raise CheckFailed("sample_run.json parses but is not a pipeline result (no requirements)")
    return f"{len(STATIC_FILES)} files present; sample_run.json parses"


def check_run(run_id: str) -> str:
    from app.runs import get_run, with_current_state
    run = get_run(run_id)
    if run is None:
        raise CheckFailed(f"run {run_id} is not in the database")
    result = with_current_state(run)   # what GET /runs/<id>/result returns
    req = result.get("requirements") or {}
    n = len(req.get("functional_requirements", [])) + len(req.get("non_functional_requirements", []))
    return f"run {run_id} loads ({run['created_at'][:19]}, {n} requirements, baseline {run['baseline_status']})"


def _get(url: str, timeout: float = 3.0) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never send localhost through a proxy
    with opener.open(url, timeout=timeout) as res:
        return res.read(65536)


def check_port(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        if s.connect_ex((host, port)) != 0:
            return f"port {port} is free"
    try:
        health = json.loads(_get(f"http://{host}:{port}/health"))
        page = _get(f"http://{host}:{port}/")
    except Exception as e:
        raise CheckFailed(f"port {port} is in use by something that is not this app ({type(e).__name__}) - "
                          f"free it (netstat -ano | findstr :{port}) or use another --port")
    if health == {"status": "ok"} and b"Requora" in page:
        return f"port {port} is already serving the app (restart it if you changed code or .env since it started)"
    raise CheckFailed(f"port {port} answers, but not as this app - free it or use another --port")


def _live_error(e: Exception) -> CheckFailed:
    text = " ".join(str(e).split())[:300]
    if "CERTIFICATE_VERIFY_FAILED" in text:
        text += " (TLS interception, e.g. an antivirus web shield, can cause this)"
    return CheckFailed(f"{type(e).__name__}: {text}")


def ping_model() -> str:
    from app import gemini_client
    start = time.perf_counter()
    try:
        with gemini_client.track_models() as used:
            reply = gemini_client.generate_text(PING_PROMPT, temperature=0)
    except Exception as e:
        raise _live_error(e)
    secs = time.perf_counter() - start
    return f"{used[-1] if used else 'unknown model'} answered in {secs:.1f} s ({len(reply.strip())} characters)"


def ping_embed() -> str:
    from app.rag import embeddings
    start = time.perf_counter()
    try:
        vectors = embeddings.embed_texts(["preflight check"])
    except Exception as e:
        raise _live_error(e)
    secs = time.perf_counter() - start
    return f"{embeddings.EMBEDDING_MODEL} returned a {len(vectors[0])}-number vector in {secs:.1f} s"


def run_check(name: str, fn, out=print) -> bool:
    try:
        message, ok = fn(), True
    except CheckFailed as e:
        message, ok = str(e), False
    except Exception as e:   # an unexpected error is a failed check, not a crash
        message, ok = f"{type(e).__name__}: {' '.join(str(e).split())[:300]}", False
    out(f"{'PASS' if ok else 'FAIL'}  {name}: {message}")
    return ok


def main(argv=None, out=print) -> int:
    ap = argparse.ArgumentParser(description="Pre-demo checks (no model calls unless --ping-model / --ping-embed).")
    ap.add_argument("--run", metavar="RUN_ID", help="also check that this stored run loads")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port to check (default {DEFAULT_PORT})")
    ap.add_argument("--ping-model", action="store_true", help="one tiny generate_text call (uses quota)")
    ap.add_argument("--ping-embed", action="store_true", help="one embed_texts call (uses quota)")
    args = ap.parse_args(argv)

    checks = [(".env", check_env), ("knowledge base", check_kb), ("database", check_db), ("static files", check_static)]
    if args.run:
        checks.append(("stored run", lambda: check_run(args.run)))
    checks.append(("port", lambda: check_port(args.port)))
    if args.ping_model:
        checks.append(("model ping", ping_model))
    if args.ping_embed:
        checks.append(("embedding ping", ping_embed))

    failed = [name for name, fn in checks if not run_check(name, fn, out)]
    out(f"{len(checks) - len(failed)} of {len(checks)} checks passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
