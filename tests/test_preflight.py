"""scripts/preflight.py: every check, offline (no model calls, no real chroma_db or data/app.db)."""
import socket
import sqlite3
import types

import pytest

import app.gemini_client as gc
from app import audit, config
from app.rag import embeddings
from app.runs import save_run
from scripts import preflight as pf
from tests.artefact_fixtures import pipeline_result

SECRET = "AIza-not-a-real-key-123456"


def _env(tmp_path, text):
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    return p


# --- .env ------------------------------------------------------------------------------

def test_env_missing_file_fails(tmp_path):
    with pytest.raises(pf.CheckFailed, match="not found"):
        pf.check_env(tmp_path / ".env", environ={})


@pytest.mark.parametrize("line", ["GEMINI_API_KEY=", "GEMINI_API_KEY=your-key-here", "GEMINI_MODEL=x"])
def test_env_without_a_real_key_fails(tmp_path, line):
    with pytest.raises(pf.CheckFailed, match="GEMINI_API_KEY is not set"):
        pf.check_env(_env(tmp_path, line + "\n"), environ={})


def test_env_shows_model_and_role_but_never_the_key(tmp_path):
    msg = pf.check_env(_env(tmp_path, f"GEMINI_API_KEY={SECRET}\nGEMINI_MODEL=m-1\nREQUIRE_ROLE=1\n"), environ={})
    assert SECRET not in msg
    assert "GEMINI_API_KEY is set" in msg and "GEMINI_MODEL=m-1" in msg and "REQUIRE_ROLE=1 (on)" in msg


def test_env_shell_values_win_over_the_file_like_the_app(tmp_path):
    path = _env(tmp_path, f"GEMINI_API_KEY={SECRET}\nGEMINI_MODEL=from-file\n")
    msg = pf.check_env(path, environ={"GEMINI_MODEL": "from-shell", "REQUIRE_ROLE": "0"})
    assert "GEMINI_MODEL=from-shell" in msg and "REQUIRE_ROLE=0 (off)" in msg
    assert "REQUIRE_ROLE=unset (off)" in pf.check_env(path, environ={})


@pytest.mark.parametrize("value", [None, "", "0", "1", "TRUE", "yes", " on ", "off", "2"])
def test_flag_rule_matches_the_app(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("REQUIRE_ROLE", raising=False)
    else:
        monkeypatch.setenv("REQUIRE_ROLE", value)
    assert pf.flag_on(value) is config.require_role()


# --- knowledge base ---------------------------------------------------------------------

class _FakeChroma:
    def __init__(self, count=None):
        self._count = count

    def __call__(self, path):
        return self

    def get_collection(self, name):
        if self._count is None:
            raise ValueError(f"Collection {name} does not exist.")
        return types.SimpleNamespace(count=lambda: self._count)


@pytest.fixture
def kb(tmp_path):
    (tmp_path / "chroma").mkdir()
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "a.txt").write_text("One.\n\nTwo.\n\nThree.\n", encoding="utf-8")
    (kb_dir / "b.txt").write_text("Four.\n\n\n\nFive.", encoding="utf-8")
    (kb_dir / "sources.json").write_text("{}", encoding="utf-8")   # not a knowledge-base document
    return tmp_path / "chroma", kb_dir


def test_expected_chunks_follow_the_ingest_split(kb):
    assert pf.expected_chunks(kb[1]) == (5, 2)


def test_real_knowledge_base_files_give_31_chunks():
    assert pf.expected_chunks(config.KNOWLEDGE_BASE_DIR)[0] == 31


def test_kb_passes_when_the_count_matches(monkeypatch, kb):
    monkeypatch.setattr("chromadb.PersistentClient", _FakeChroma(5))
    assert "compliance_kb has 5 chunks" in pf.check_kb(*kb)


def test_kb_fails_on_a_stale_count(monkeypatch, kb):
    monkeypatch.setattr("chromadb.PersistentClient", _FakeChroma(4))
    with pytest.raises(pf.CheckFailed, match="has 4 chunks but the 2 knowledge-base files give 5"):
        pf.check_kb(*kb)


def test_kb_fails_without_the_collection_or_the_folder(monkeypatch, kb, tmp_path):
    monkeypatch.setattr("chromadb.PersistentClient", _FakeChroma(None))
    with pytest.raises(pf.CheckFailed, match="collection compliance_kb not found"):
        pf.check_kb(*kb)
    with pytest.raises(pf.CheckFailed, match="app.rag.ingest"):
        pf.check_kb(tmp_path / "nowhere", kb[1])


# --- database ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    return tmp_path / "app.db"


def test_db_missing_fails_and_is_not_created(db):
    with pytest.raises(pf.CheckFailed, match="not found"):
        pf.check_db()
    assert not db.exists()


def test_db_passes_with_tables_and_an_intact_chain(db):
    audit.append_event("test_event", "auditor")
    audit.append_event("test_event", "auditor")
    assert pf.check_db() == "app.db opens; 0 run(s); audit chain ok (2 events)"


def test_db_fails_on_a_broken_chain(db):
    audit.append_event("test_event", "auditor")
    audit.append_event("test_event", "auditor")
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TRIGGER audit_no_update")
    conn.execute("UPDATE audit_events SET role = 'architect' WHERE seq = 1")
    conn.commit()
    conn.close()
    with pytest.raises(pf.CheckFailed, match="audit chain broken at seq 1"):
        pf.check_db()


def test_db_fails_on_missing_tables_or_a_non_database_file(db):
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE runs (run_id TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(pf.CheckFailed, match="missing table\\(s\\): approval_items, decisions, audit_events"):
        pf.check_db()
    db.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(pf.CheckFailed, match="does not open"):
        pf.check_db()


# --- static files ------------------------------------------------------------------------

def _static(tmp_path, sample='{"requirements": {}}'):
    for f in pf.STATIC_FILES:
        (tmp_path / f).write_text("x", encoding="utf-8")
    (tmp_path / "sample_run.json").write_text(sample, encoding="utf-8")
    return tmp_path


def test_static_passes_for_the_real_files():
    assert "sample_run.json parses" in pf.check_static()


def test_static_fails_on_a_missing_file_or_a_bad_sample(tmp_path):
    d = _static(tmp_path)
    assert "5 files present" in pf.check_static(d)
    (d / "charts.js").unlink()
    with pytest.raises(pf.CheckFailed, match="charts.js"):
        pf.check_static(d)
    d = _static(tmp_path, "{not json")
    with pytest.raises(pf.CheckFailed, match="does not parse"):
        pf.check_static(d)
    d = _static(tmp_path, "[]")
    with pytest.raises(pf.CheckFailed, match="not a pipeline result"):
        pf.check_static(d)


# --- stored run --------------------------------------------------------------------------

def test_run_loads_or_fails(db):
    save_run("run-preflight", pipeline_result(), "business analyst")
    msg = pf.check_run("run-preflight")
    assert msg.startswith("run run-preflight loads") and "baseline open" in msg
    with pytest.raises(pf.CheckFailed, match="not in the database"):
        pf.check_run("no-such-run")


# --- port ----------------------------------------------------------------------------------

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def listening():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(8)   # several checks connect without accepting
    yield s.getsockname()[1]
    s.close()


def test_port_free_passes():
    port = _free_port()
    assert pf.check_port(port) == f"port {port} is free"


def test_port_serving_the_app_passes(monkeypatch, listening):
    pages = {"/health": b'{"status":"ok"}', "/": b"<title>Requora AI - Requirements</title>"}
    monkeypatch.setattr(pf, "_get", lambda url, timeout=3.0: pages[url.split(str(listening), 1)[1]])
    assert "already serving the app" in pf.check_port(listening)


def test_port_held_by_something_else_fails(monkeypatch, listening):
    monkeypatch.setattr(pf, "_get", lambda url, timeout=3.0: b'{"ok": true}')
    with pytest.raises(pf.CheckFailed, match="not as this app"):
        pf.check_port(listening)

    def no_http(url, timeout=3.0):
        raise ConnectionResetError("not http")
    monkeypatch.setattr(pf, "_get", no_http)
    with pytest.raises(pf.CheckFailed, match="in use by something that is not this app"):
        pf.check_port(listening)


# --- live pings (mocked) -----------------------------------------------------------------

def test_ping_model_reports_the_model_that_answered(monkeypatch):
    prompts = []

    def generate_content(model, contents, config):
        prompts.append(contents)
        return types.SimpleNamespace(text="OK")
    monkeypatch.setattr(gc, "_client", types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate_content)))
    msg = pf.ping_model()
    assert msg.startswith(gc.GEMINI_MODEL + " answered in ") and "(2 characters)" in msg
    assert len(prompts) == 1


def test_ping_failures_are_short_and_hint_at_tls(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("SSLError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed " + "x" * 1000)
    monkeypatch.setattr(gc, "generate_text", broken)
    with pytest.raises(pf.CheckFailed) as e:
        pf.ping_model()
    assert "TLS interception" in str(e.value) and len(str(e.value)) < 450


def test_ping_embed_reports_the_vector_size(monkeypatch):
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [[0.0] * 768 for _ in texts])
    assert pf.ping_embed().startswith("gemini-embedding-001 returned a 768-number vector")


# --- main ----------------------------------------------------------------------------------

@pytest.fixture
def stubbed(monkeypatch):
    """Every check passes and records that it ran; tests flip individual ones to failing."""
    ran = []

    def ok(name):
        def fn(*a):
            ran.append(name)
            return name + " fine"
        return fn
    for name in ("check_env", "check_kb", "check_db", "check_static", "check_run", "check_port", "ping_model", "ping_embed"):
        monkeypatch.setattr(pf, name, ok(name))
    return ran


def test_main_runs_local_checks_only_by_default(stubbed):
    lines = []
    assert pf.main([], out=lines.append) == 0
    assert stubbed == ["check_env", "check_kb", "check_db", "check_static", "check_port"]   # no model calls
    assert all(line.startswith("PASS  ") for line in lines[:-1]) and lines[-1] == "5 of 5 checks passed"


def test_main_adds_run_and_pings_only_when_asked(stubbed):
    assert pf.main(["--run", "abc", "--ping-model", "--ping-embed", "--port", "9000"], out=lambda s: None) == 0
    assert stubbed[-4:] == ["check_run", "check_port", "ping_model", "ping_embed"]


def test_main_exits_non_zero_on_any_failure_and_keeps_going(stubbed, monkeypatch):
    def fail():
        raise pf.CheckFailed("chroma_db/ not found")

    def crash():
        raise KeyError("boom")
    monkeypatch.setattr(pf, "check_kb", fail)
    monkeypatch.setattr(pf, "check_static", crash)
    lines = []
    assert pf.main([], out=lines.append) == 1
    assert "FAIL  knowledge base: chroma_db/ not found" in lines
    assert "FAIL  static files: KeyError: 'boom'" in lines
    assert "check_port" in stubbed   # later checks still ran
    assert lines[-1] == "3 of 5 checks passed; failed: knowledge base, static files"
