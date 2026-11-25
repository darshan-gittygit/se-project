"""Phase 2 step 3: risk register (score and rank in code) and the anchored risk_level rubric. Offline."""
import json

import pytest
from fastapi.testclient import TestClient

import app.agents.risk_agent as ra
from app.agents.requirement_agent import PROMPT_TEMPLATE as REQUIREMENT_PROMPT
from app.main import app
from tests.helpers import risk

client = TestClient(app)
REQS = [{"id": "FR-1", "requirement": "Verify identity documents.", "risk_level": "High"},
        {"id": "NFR-1", "requirement": "Score within 5 seconds.", "risk_level": "Medium"}]


def scripted(*replies):
    calls = []

    def fake(prompt, temperature=0.3):
        calls.append(prompt)
        r = replies[min(len(calls), len(replies)) - 1]
        return r if isinstance(r, str) else json.dumps(r)
    return fake, calls


THREE = {"risks": [risk("A", 2, 2), risk("B", 5, 4, ["FR-1", "NFR-1"], "Compliance"), risk("C", 3, 3, category="Security")]}


def build(monkeypatch, reply, **kw):
    fake, calls = scripted(reply)
    monkeypatch.setattr(ra, "generate_text", fake)
    return ra.build_risk_register("Loans", REQS, **kw), calls


def test_score_level_rank_and_ids_are_computed_in_code(monkeypatch):
    reg, _ = build(monkeypatch, THREE)
    rows = reg["risks"]
    assert [(r["id"], r["rank"], r["description"], r["score"], r["level"]) for r in rows] == [
        ("RISK-1", 1, "B", 20, "High"), ("RISK-2", 2, "C", 9, "Medium"), ("RISK-3", 3, "A", 4, "Low")]
    assert set(rows[0]) == {"id", "rank", "description", "category", "likelihood", "likelihood_reason", "impact",
                            "impact_reason", "score", "level", "mitigation", "linked_requirement_ids"}
    assert reg["summary"] == {"count": 3, "by_level": {"High": 1, "Medium": 1, "Low": 1},
                              "likelihood_distribution": {"1": 0, "2": 1, "3": 1, "4": 0, "5": 1},
                              "impact_distribution": {"1": 0, "2": 1, "3": 1, "4": 1, "5": 0}}


@pytest.mark.parametrize("score, level", [(1, "Low"), (4, "Low"), (5, "Medium"), (9, "Medium"), (10, "High"), (25, "High")])
def test_level_bands(score, level):
    assert ra.level_for(score) == level


def test_equal_scores_rank_by_impact_then_likelihood(monkeypatch):
    reg, _ = build(monkeypatch, {"risks": [risk("low impact", 4, 2), risk("high impact", 2, 4), risk("other", 1, 1)]})
    assert [r["description"] for r in reg["risks"]] == ["high impact", "low impact", "other"]


def test_model_supplied_score_id_and_level_are_ignored(monkeypatch):
    sneaky = dict(risk("A", 1, 2), score=25, level="High", id="RISK-99")
    reg, _ = build(monkeypatch, {"risks": [sneaky, risk("B", 1, 1), risk("C", 1, 3)]})
    a = next(r for r in reg["risks"] if r["description"] == "A")
    assert a["score"] == 2 and a["level"] == "Low" and a["id"] != "RISK-99"


def test_prompt_carries_rubrics_gaps_and_quality_flags(monkeypatch):
    mapping = {"requirements": [{"requirement_id": "FR-1", "needs_human_review": True,
                                 "review_reasons": ["kyc_aml: low-confidence match (distance 0.58)"],
                                 "mappings": [{"status": "gap", "source": "kyc_aml", "compliance_gap": "No address check."}]}]}
    quality = {"flags": [{"type": "conflict", "requirement_ids": ["FR-1", "NFR-1"], "reason": "cannot both hold"}]}
    _, calls = build(monkeypatch, THREE, scenario="A loan system.", compliance_mapping=mapping, quality=quality)
    p = calls[0]
    assert "4 = Likely: the input itself points to it" in p and "5 = Severe" in p
    assert "FR-1: gap against kyc_aml: No address check." in p
    # retrieval-confidence review flags are not risks; they inflated likelihood in the live run
    assert "low-confidence match" not in p and "needs review" not in p
    assert "conflict (FR-1, NFR-1): cannot both hold" in p
    assert "- FR-1 [High]: Verify identity documents." in p


@pytest.mark.parametrize("bad", [
    {"risks": [risk("A", 1, 1), risk("B", 1, 1)]},                                    # fewer than 3
    {"risks": [risk(str(i), 1, 1) for i in range(11)]},                               # more than 10
    {"risks": [risk("A", 0, 2), risk("B", 1, 1), risk("C", 1, 1)]},                   # likelihood 0
    {"risks": [risk("A", 6, 2), risk("B", 1, 1), risk("C", 1, 1)]},                   # likelihood 6
    {"risks": [risk("A", "3", 2), risk("B", 1, 1), risk("C", 1, 1)]},                 # string score
    {"risks": [risk("A", 3.5, 2), risk("B", 1, 1), risk("C", 1, 1)]},                 # fractional
    {"risks": [risk("A", 2, 2, ["FR-9"]), risk("B", 1, 1), risk("C", 1, 1)]},         # unknown requirement
    {"risks": [risk("A", 2, 2, []), risk("B", 1, 1), risk("C", 1, 1)]},               # no link
    {"risks": [risk("A", 2, 2, category="Reputational"), risk("B", 1, 1), risk("C", 1, 1)]},
    {"risks": [dict(risk("A", 2, 2), mitigation=""), risk("B", 1, 1), risk("C", 1, 1)]},
    {"risks": [dict(risk("A", 2, 2), impact={"score": 2, "reason": " "}), risk("B", 1, 1), risk("C", 1, 1)]},
    {"risks": [risk("Same", 2, 2), risk("same", 1, 1), risk("C", 1, 1)]},             # repeated
])
def test_invalid_register_is_rejected(bad):
    with pytest.raises(ValueError):
        ra.RiskOutput.model_validate(bad, context={"requirement_ids": {"FR-1", "NFR-1"}})


def test_category_case_is_normalised(monkeypatch):
    reg, _ = build(monkeypatch, {"risks": [risk("A", 1, 1, category="compliance"), risk("B", 1, 1), risk("C", 1, 1)]})
    assert {r["category"] for r in reg["risks"]} == {"Compliance", "Technical"}


def test_retry_then_recover(monkeypatch):
    fake, calls = scripted({"risks": [risk("A", 9, 1)]}, THREE)
    monkeypatch.setattr(ra, "generate_text", fake)
    assert ra.build_risk_register("Loans", REQS)["summary"]["count"] == 3 and len(calls) == 2
    assert "rejected" in calls[1]


# --- pipeline: retry then 502 ----------------------------------------------------

def test_pipeline_risk_step_retries_then_502(monkeypatch):
    import tests.test_pipeline_phase1 as p1
    seen = []
    fake = p1.router(seen=seen)
    for mod in (p1.req_mod, p1.comp_mod, p1.quality_mod, p1.sdlc_mod):
        monkeypatch.setattr(mod, "generate_text", fake)
    monkeypatch.setattr(ra, "generate_text", lambda p, temperature=0.3: seen.append(p) or json.dumps({"risks": []}))
    monkeypatch.setattr(p1.comp_mod, "retrieve_many", lambda qs, top_k=3: [[] for _ in qs])
    r = client.post("/agents/pipeline", json={"scenario": p1.SCENARIO})
    assert r.status_code == 502
    assert r.json()["detail"].startswith("Risk register failed: model output failed validation")
    assert sum("threat and risk register" in p for p in seen) == 2
    assert not any("9 characteristics" in p for p in seen)


def test_pipeline_lifts_compliance_mapping_to_the_top_level(monkeypatch):
    import tests.test_pipeline_phase1 as p1
    fake = p1.router()
    for mod in (p1.req_mod, p1.comp_mod, p1.quality_mod, p1.sdlc_mod, ra):
        monkeypatch.setattr(mod, "generate_text", fake)
    monkeypatch.setattr(p1.comp_mod, "retrieve_many", lambda qs, top_k=3: [[] for _ in qs])
    d = client.post("/agents/pipeline", json={"scenario": p1.SCENARIO}).json()
    assert "compliance_mapping" not in d["compliance"]
    assert [e["requirement_id"] for e in d["compliance_mapping"]["requirements"]] == ["FR-1", "FR-2", "NFR-1"]
    assert d["risk_register"]["risks"][0]["id"] == "RISK-1"


# --- the requirement risk_level rubric ------------------------------------------

def test_requirement_risk_level_is_anchored_and_not_inflated_by_domain():
    for anchor in ("High: the failure directly causes", "Medium: the failure causes noticeable but recoverable harm",
                   "Low: the failure is an inconvenience"):
        assert anchor in REQUIREMENT_PROMPT
    assert "regulated domain is not by itself a reason for High" in REQUIREMENT_PROMPT
