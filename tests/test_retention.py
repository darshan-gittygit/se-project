"""Phase 5B step 4: retention purge. Offline."""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import audit, config, retention
from app.db import connect
from app.runs import get_run, item_versions, new_run_id, save_run
from tests.artefact_fixtures import pipeline_result


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "retention.db")
    return tmp_path / "retention.db"


def make_run(age_days, frozen=False):
    rid = new_run_id()
    save_run(rid, pipeline_result(), "business analyst")
    created = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat(timespec="microseconds")
    with connect() as conn:   # runs is not append-only; backdating it simulates an old run
        conn.execute("UPDATE runs SET created_at = ?, baseline_status = ? WHERE run_id = ?",
                     (created, "frozen" if frozen else "open", rid))
        conn.execute("INSERT INTO decisions (run_id, item_type, item_id, version, action, role, comment, created_at) "
                     "VALUES (?, 'requirement', 'FR-1', 1, 'accept', 'product owner', '', ?)", (rid, created))
    audit.append_event("approval_accept", "product owner", run_id=rid)
    return rid


def test_purge_deletes_old_runs_and_items_but_keeps_decisions_and_audit(db):
    old, recent = make_run(400), make_run(10)
    out = retention.purge(days=365)
    assert out["purged_run_ids"] == [old] and out["kept_frozen_run_ids"] == []
    assert get_run(old) is None and item_versions(old) == []
    assert get_run(recent) is not None and item_versions(recent)
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM decisions WHERE run_id = ?", (old,)).fetchone()[0] == 1
    assert audit.query_events(run_id=old)                       # history of the purged run is kept
    purge_event = audit.query_events(action="runs_purged")[-1]
    assert purge_event["role"] == "system" and purge_event["details"]["purged_run_ids"] == [old]
    assert audit.verify_chain()["ok"] is True


def test_triggers_stay_intact_after_a_purge(db):
    make_run(400)
    retention.purge(days=30)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with connect() as conn:
            conn.execute("DELETE FROM audit_events")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with connect() as conn:
            conn.execute("DELETE FROM decisions")


def test_frozen_baselines_are_kept_unless_asked(db):
    frozen = make_run(400, frozen=True)
    out = retention.purge(days=365)
    assert out["purged_run_ids"] == [] and out["kept_frozen_run_ids"] == [frozen]
    assert retention.purge(days=365, include_frozen=True)["purged_run_ids"] == [frozen]
    assert get_run(frozen) is None


def test_dry_run_deletes_nothing_and_logs_nothing(db):
    old = make_run(400)
    before = len(audit.query_events())
    out = retention.purge(days=365, dry_run=True)
    assert out["purged_run_ids"] == [old] and get_run(old) is not None
    assert len(audit.query_events()) == before


def test_retention_days_setting(db, monkeypatch):
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    assert config.retention_days() is None
    with pytest.raises(ValueError, match="--days"):
        retention.purge()
    monkeypatch.setenv("RETENTION_DAYS", "90")
    old = make_run(100)
    assert config.retention_days() == 90 and retention.purge()["purged_run_ids"] == [old]
    monkeypatch.setenv("RETENTION_DAYS", "0")
    with pytest.raises(ValueError):
        config.retention_days()


def test_command_line(db):
    make_run(400)
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "REQUORA_DB_PATH": str(db), "GEMINI_API_KEY": "x"}
    env.pop("RETENTION_DAYS", None)
    run = lambda *args: subprocess.run([sys.executable, "-m", "app.retention", *args], cwd=root, env=env,
                                       capture_output=True, text=True, timeout=120)
    dry = run("--days", "365", "--dry-run")
    assert dry.returncode == 0 and dry.stdout.startswith("Would delete 1 run(s)")
    real = run("--days", "365")
    assert real.returncode == 0 and real.stdout.startswith("Deleted 1 run(s)")
    missing = run()
    assert missing.returncode == 2 and "RETENTION_DAYS" in missing.stderr
