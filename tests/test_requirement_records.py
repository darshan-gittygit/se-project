"""Phase 1 step 1: requirement records with the problem statement's section-8 fields. Offline."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.agents.requirement_agent as req_mod
from app.main import app
from app.schemas import RequirementsOutput
from app.vocabulary import INTERVIEW_TOPICS, REQUIREMENT_CATEGORIES, STAKEHOLDER_ROLES
from tests.helpers import record

client = TestClient(app)
SCENARIO = "A loan origination system that verifies applicant identity documents and calculates a credit score."
CTX = {"source_texts": [{"text": SCENARIO, "role": None},
                        {"text": "Loans above 5 lakh need a second approver.", "role": "Risk-management teams"}]}
SECTION_8_KEYS = {
    "id", "requirement", "priority", "type", "categories", "source_stakeholder", "source_text",
    "business_justification", "dependencies", "assumptions", "acceptance_criteria",
    "applicable_regulations", "risk_level", "confidence", "approval_status",
}


def output(frs, nfrs=None):
    return {"domain_inferred": "Loans", "assumptions": [],
            "functional_requirements": frs,
            "non_functional_requirements": nfrs or [record("NFR-1", "Score within 2 seconds.")]}


def validate(frs, nfrs=None, ctx=CTX):
    return RequirementsOutput.model_validate(output(frs, nfrs), context=ctx).model_dump()


def test_vocabularies_are_quoted_from_the_problem_statement():
    text = (Path(__file__).resolve().parent.parent / "docs" / "PROBLEM_STATEMENT.md").read_text(encoding="utf-8")
    for label in STAKEHOLDER_ROLES + INTERVIEW_TOPICS:
        assert label in text, label
    for label in REQUIREMENT_CATEGORIES:
        assert f"{label} requirements" in text, label


def test_record_has_every_section_8_field_and_keeps_the_old_keys():
    fr = validate([record("FR-1", "Verify documents.", source_text="verifies applicant identity documents", assumptions=[])])["functional_requirements"][0]
    assert set(fr) == SECTION_8_KEYS
    assert {"id", "requirement", "priority"} <= set(fr)


def test_code_set_fields_override_whatever_the_llm_sends():
    fr = validate([record("FR-1", "x", approval_status="accepted", applicable_regulations=["RBI Master Direction"],
                          confidence="High", type="NFR")])["functional_requirements"][0]
    assert fr["approval_status"] == "proposed"
    assert fr["applicable_regulations"] == []
    assert fr["type"] == "FR"
    assert fr["confidence"] == "Low"  # no quote, assumption only


@pytest.mark.parametrize("source_text, assumptions, expected", [
    ("verifies applicant identity documents", [], "High"),
    ("calculates a credit score", ["bureau data is available"], "Medium"),
    ("", ["inferred from the domain"], "Low"),
])
def test_confidence_is_computed_from_grounding(source_text, assumptions, expected):
    fr = validate([record("FR-1", "x", source_text=source_text, assumptions=assumptions)])["functional_requirements"][0]
    assert fr["confidence"] == expected


def test_quote_matching_ignores_case_and_punctuation():
    validate([record("FR-1", "x", source_text="Verifies applicant identity-documents", assumptions=[])])


def test_quote_from_an_answer_sets_the_stakeholder_to_who_said_it():
    fr = validate([record("FR-1", "x", source_text="need a second approver", source_stakeholder="Product owners",
                          assumptions=[])])["functional_requirements"][0]
    assert fr["source_stakeholder"] == "Risk-management teams"


def test_labels_are_matched_case_insensitively_and_deduplicated():
    fr = validate([record("FR-1", "x", categories=["security", "Security", "audit and reporting"],
                          source_stakeholder="compliance and legal officers", risk_level="high")])["functional_requirements"][0]
    assert fr["categories"] == ["Security", "Audit and reporting"]
    assert fr["source_stakeholder"] == "Compliance and legal officers" and fr["risk_level"] == "High"


@pytest.mark.parametrize("bad", [
    record("FR-1", "x", source_text="approves loans instantly"),            # not a quote
    record("FR-1", "x", source_text="loan"),                                  # too short to trace
    record("FR-1", "x", source_text="", assumptions=[]),                     # neither quote nor assumption
    record("FR-1", "x", categories=[]),
    record("FR-1", "x", categories=["Blockchain"]),
    record("FR-1", "x", source_stakeholder="The CEO"),
    record("FR-1", "x", risk_level="Critical"),
    record("FR-1", "x", acceptance_criteria=[]),
    record("FR-1", "x", acceptance_criteria=["  "]),
    record("FR-1", "x", business_justification=""),
    record("FR-1", "x", dependencies=["FR-9"]),                              # unknown id
    record("FR-1", "x", dependencies=["FR-1"]),                              # itself
])
def test_invalid_records_are_rejected(bad):
    with pytest.raises(ValueError):
        validate([bad])


def test_dependencies_may_point_across_lists():
    out = validate([record("FR-1", "x", dependencies=["NFR-1"])])
    assert out["functional_requirements"][0]["dependencies"] == ["NFR-1"]


def scripted(*replies):
    calls = []

    def fake(prompt, temperature=0.3):
        calls.append(prompt)
        return json.dumps(replies[min(len(calls), len(replies)) - 1])
    return fake, calls


def test_endpoint_retries_an_invented_quote_then_succeeds(monkeypatch):
    bad = output([record("FR-1", "x", source_text="instant approval for everyone", assumptions=[])])
    good = output([record("FR-1", "x", source_text="calculates a credit score", assumptions=[])])
    fake, calls = scripted(bad, good)
    monkeypatch.setattr(req_mod, "generate_text", fake)
    r = client.post("/agents/requirements", json={"scenario": SCENARIO})
    assert r.status_code == 200 and len(calls) == 2
    assert "not an exact quote" in calls[1]
    assert r.json()["functional_requirements"][0]["confidence"] == "High"


def test_endpoint_returns_502_after_two_invalid_records(monkeypatch):
    fake, calls = scripted(output([record("FR-1", "x", categories=["Blockchain"])]))
    monkeypatch.setattr(req_mod, "generate_text", fake)
    r = client.post("/agents/requirements", json={"scenario": SCENARIO})
    assert r.status_code == 502 and len(calls) == 2
    assert r.json()["detail"].startswith("Agent 1 failed: model output failed validation")


def test_answers_reach_the_prompt_and_can_be_quoted(monkeypatch):
    answer = {"role": "Risk-management teams", "question": "What limits apply?",
              "answer": "Loans above 5 lakh need a second approver."}
    good = output([record("FR-1", "x", source_text="above 5 lakh need a second approver", assumptions=[])])
    fake, calls = scripted(good)
    monkeypatch.setattr(req_mod, "generate_text", fake)
    r = client.post("/agents/requirements", json={"scenario": SCENARIO, "answers": [answer]})
    assert r.status_code == 200
    assert "[Risk-management teams] Q: What limits apply?" in calls[0]
    assert r.json()["functional_requirements"][0]["source_stakeholder"] == "Risk-management teams"


@pytest.mark.parametrize("answers", [
    [{"role": "The CEO", "question": "q", "answer": "a"}],
    [{"role": "Product owners", "question": "q", "answer": "a", "topic": "Vibes"}],
])
def test_answers_with_unknown_role_or_topic_are_rejected(answers):
    assert client.post("/agents/requirements", json={"scenario": SCENARIO, "answers": answers}).status_code == 422


@pytest.mark.parametrize("answers", [
    [{"role": "Product owners", "question": "q", "answer": "   "}],
    [{"role": "Product owners", "question": "q", "answer": "a"}] * 31,
    [{"role": "Product owners", "question": "q", "answer": "a" * 2001}],
])
def test_empty_too_many_or_too_long_answers_get_a_400(answers):
    r = client.post("/agents/requirements", json={"scenario": SCENARIO, "answers": answers})
    assert r.status_code == 400 and isinstance(r.json()["detail"], str)
