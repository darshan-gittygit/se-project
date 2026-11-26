"""make_sample_run --from-live: builds the UI's recorded sample from a saved live pipeline response. Offline."""
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "app" / "static" / "sample_run.json"


@pytest.fixture
def msr(monkeypatch):
    # importing the script points REQUORA_DB_PATH at a throwaway file; keep the test session's value afterwards
    monkeypatch.setenv("REQUORA_DB_PATH", os.environ["REQUORA_DB_PATH"])
    import scripts.make_sample_run as m
    return m


def live_body():
    body = json.loads(SAMPLE.read_text(encoding="utf-8"))
    body.pop("_sample", None)
    return body


def test_from_live_labels_and_keeps_the_result(msr, tmp_path):
    src = tmp_path / "run.json"
    src.write_text(json.dumps({"status": 200, "secs": 85.5, "recorded_at": "2026-11-26", "body": live_body()}), encoding="utf-8")
    out = tmp_path / "sample.json"
    msr.from_live(src, out)
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["_sample"]["label"] == "Recorded sample, not a live run"
    assert "2026-11-26" in d["_sample"]["note"] and "85.5 s" in d["_sample"]["note"]
    assert {k: v for k, v in d.items() if k != "_sample"} == live_body()   # nothing else changed


def test_from_live_accepts_a_raw_response(msr, tmp_path):
    src = tmp_path / "raw.json"
    src.write_text(json.dumps(live_body()), encoding="utf-8")
    msr.from_live(src, tmp_path / "s.json")
    assert json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))["_sample"]["label"]


@pytest.mark.parametrize("leak,msg", [
    ("call ravi.k@example.com", "unmasked sensitive data"),
    ("card 4111 1111 1111 1111", "unmasked sensitive data"),
    ("key AIzaSyA-not-a-real-key-0000000000000", "API key"),
])
def test_from_live_refuses_unmasked_or_key_like_content(msr, tmp_path, leak, msg):
    body = live_body()
    body["requirements"]["functional_requirements"][0]["business_justification"] = leak
    src, out = tmp_path / "run.json", tmp_path / "s.json"
    src.write_text(json.dumps({"body": body}), encoding="utf-8")
    with pytest.raises(SystemExit, match=msg):
        msr.from_live(src, out)
    assert not out.exists()


def test_from_live_refuses_an_incomplete_response(msr, tmp_path):
    src = tmp_path / "err.json"
    src.write_text(json.dumps({"status": 502, "body": {"detail": "Agent 1 failed"}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="not a full pipeline response"):
        msr.from_live(src, tmp_path / "s.json")
