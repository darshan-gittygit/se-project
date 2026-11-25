"""Phase 4 steps 4-5: /agents/pipeline artefact keys and SRS download endpoints. Offline."""
import json

import pytest
from fastapi.testclient import TestClient

import app.agents.artefact_agent as aa
import app.main as main_mod
import tests.test_pipeline_phase1 as p1
from app.main import app
from tests.artefact_fixtures import pipeline_result

client = TestClient(app)
GENERATED_KEYS = {"user_stories", "use_cases", "process_workflows", "data_requirements", "interface_requirements"}
CODE_KEYS = {"traceability_matrix", "compliance_control_matrix", "assumptions_register", "open_issues"}


@pytest.fixture
def pipeline(monkeypatch):
    import app.agents.risk_agent as ra
    import app.agents.workflow_agent as wa
    seen = []

    def run(artefact_reply=None):
        fake = p1.router(seen=seen)
        for mod in (p1.req_mod, p1.comp_mod, p1.quality_mod, p1.sdlc_mod, ra, wa, aa):
            monkeypatch.setattr(mod, "generate_text", fake)
        if artefact_reply is not None:
            monkeypatch.setattr(aa, "generate_text", lambda p, temperature=0.3: seen.append(p) or json.dumps(artefact_reply))
        monkeypatch.setattr(p1.comp_mod, "retrieve_many", lambda qs, top_k=3: [[] for _ in qs])
        return client.post("/agents/pipeline", json={"scenario": p1.SCENARIO})
    run.seen = seen
    return run


def test_pipeline_returns_every_artefact_and_srs_links(pipeline):
    d = pipeline().json()
    art = d["artefacts"]
    assert set(art) == GENERATED_KEYS | CODE_KEYS | {"generation"}
    assert art["generation"]["llm_calls"] == 1
    assert [r["requirement_id"] for r in art["traceability_matrix"]] == ["FR-1", "FR-2", "NFR-1"]
    assert art["traceability_matrix"][0]["user_stories_ids"] == ["US-1"]
    assert art["user_stories"][0]["source_statements"][0]["statement"] == "verifies applicant identity documents"
    srs = d["srs"]
    assert srs["markdown_url"] == f"/agents/srs/{srs['id']}?format=markdown"
    assert srs["html_url"] == f"/agents/srs/{srs['id']}?format=html"


def test_get_srs_markdown_and_html(pipeline):
    srs = pipeline().json()["srs"]
    r = client.get(srs["markdown_url"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"] == f'attachment; filename="requora-srs-{srs["id"]}.md"'
    assert r.text.startswith("# Software Requirements Specification: Digital Loan Origination")
    assert "## 17. Approval status" in r.text and "US-1" in r.text
    h = client.get(srs["html_url"])
    assert h.status_code == 200 and h.headers["content-type"].startswith("text/html")
    assert h.text.startswith("<!DOCTYPE html>") and "@media print" in h.text


def test_unknown_srs_id_is_404_and_bad_format_is_422():
    assert client.get("/agents/srs/doesnotexist").status_code == 404
    assert client.get("/agents/srs/doesnotexist?format=docx").status_code == 422


def test_srs_store_evicts_old_runs(pipeline, monkeypatch):
    monkeypatch.setattr(main_mod, "SRS_STORE", main_mod.SrsStore(capacity=1))
    first = pipeline().json()["srs"]["id"]
    pipeline()
    assert client.get(f"/agents/srs/{first}").status_code == 404


@pytest.mark.parametrize("fmt, kind", [("markdown", "text/markdown"), ("html", "text/html")])
def test_post_srs_from_a_saved_result(fmt, kind):
    r = client.post(f"/agents/srs?format={fmt}", json=pipeline_result())
    assert r.status_code == 200 and r.headers["content-type"].startswith(kind)
    assert "Not a legal determination." in r.text


@pytest.mark.parametrize("body", [
    {},
    {"requirements": "nope"},
    {"requirements": {"functional_requirements": []}},
    {"requirements": {"functional_requirements": [{"id": "FR-1"}], "non_functional_requirements": []}},  # no statement text
])
def test_post_srs_rejects_bodies_that_are_not_pipeline_results(body):
    r = client.post("/agents/srs", json=body)
    assert r.status_code == 400 and isinstance(r.json()["detail"], str)


def test_pipeline_artefact_step_retries_then_502(pipeline):
    r = pipeline(artefact_reply={"user_stories": [], "use_cases": []})
    assert r.status_code == 502
    assert r.json()["detail"].startswith("Artefact generator failed: model output failed validation")
    assert sum("writing the artefacts of a Software Requirements Specification" in p for p in pipeline.seen) == 2
