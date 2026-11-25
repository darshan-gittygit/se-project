"""Phase 1 step 3: quality and conflict checks. Offline."""
import json

import pytest
from fastapi.testclient import TestClient

import app.agents.quality_agent as qa
from app.main import app

client = TestClient(app)


def req(rid, text, criteria=None, categories=None):
    return {"id": rid, "requirement": text, "acceptance_criteria": criteria or [], "categories": categories or []}


def scripted(*replies):
    calls = []

    def fake(prompt, temperature=0.3):
        calls.append(prompt)
        r = replies[min(len(calls), len(replies)) - 1]
        return r if isinstance(r, str) else json.dumps(r)
    return fake, calls


NO_CONFLICTS = {"conflicts": []}


# --- ambiguity (rule) ---------------------------------------------------------

def test_vague_terms_are_flagged_once_per_requirement():
    (f,) = qa.ambiguity_flags([req("NFR-1", "The system shall be fast, user-friendly and fast to learn, etc.")])
    assert f["requirement_ids"] == ["NFR-1"] and f["type"] == "ambiguity" and f["detected_by"] == "rule"
    assert f["reason"] == "Vague term(s): fast, user-friendly, etc."
    assert f["suggested_fix"]


@pytest.mark.parametrize("text", [
    "The system shall show the breakfast menu.",              # 'fast' inside a word
    "The system shall respond within 2 seconds.",
    "The system shall reject applications missing a PAN.",
])
def test_precise_wording_is_not_flagged(text):
    assert qa.ambiguity_flags([req("FR-1", text)]) == []


def test_fix_points_to_a_measurable_acceptance_criterion_when_there_is_one():
    (f,) = qa.ambiguity_flags([req("NFR-1", "Scoring shall be fast.", ["95% of scores within 3 seconds"])])
    assert "95% of scores within 3 seconds" in f["suggested_fix"]


# --- untestable (rule) ----------------------------------------------------------

@pytest.mark.parametrize("category", ["Performance", "Availability and reliability"])
def test_measurable_category_nfr_without_any_number_is_untestable(category):
    (f,) = qa.untestable_flags([req("NFR-1", "The system shall be highly reliable.", ["It rarely fails"], [category, "Security"])])
    assert f["type"] == "untestable" and f["requirement_ids"] == ["NFR-1"]


def test_number_in_statement_or_criteria_makes_it_testable():
    assert qa.untestable_flags([req("NFR-1", "Availability shall be 99.9% per month.", categories=["Availability and reliability"])]) == []
    assert qa.untestable_flags([req("NFR-2", "Pages shall load quickly.", ["p95 load time under 2 s"], ["Performance"])]) == []


@pytest.mark.parametrize("categories", [["Audit and reporting", "Security", "Regulatory"], ["Privacy"], []])
def test_other_or_missing_categories_are_not_checked_for_numbers(categories):
    # Live loan run: "maintain an immutable audit trail" is testable without a number.
    nfr = req("NFR-1", "The system shall maintain an immutable audit trail of every credit decision.",
              ["Audit logs cannot be modified or deleted by standard system users"], categories)
    assert qa.untestable_flags([nfr]) == []


# --- duplicates (rule) ----------------------------------------------------------

def test_near_identical_wording_is_a_duplicate():
    (f,) = qa.duplicate_flags([req("FR-1", "The system shall allow users to place an order."),
                               req("FR-2", "The system must allow users to place orders.")])
    assert f["type"] == "duplicate" and f["requirement_ids"] == ["FR-1", "FR-2"]
    assert "similarity 0.9" in f["reason"]


@pytest.mark.parametrize("a, b", [
    ("The system shall encrypt all payment data in transit and at rest.",
     "The system shall encrypt all user data in transit and at rest."),     # 0.90: different data
    ("The system shall verify identity documents.", "The system shall verify proof of address."),
])
def test_similar_but_different_requirements_are_not_duplicates(a, b):
    assert qa.duplicate_flags([req("FR-1", a), req("FR-2", b)]) == []


# --- conflicts (one LLM call) ----------------------------------------------------

REQS = {"domain": "Loans",
        "functional_requirements": [req("FR-1", "The system shall approve loans instantly."),
                                    req("FR-2", "The system shall hold every loan for manual review.")],
        "non_functional_requirements": [req("NFR-1", "The system shall respond within 1 second.", ["p95 < 1 s"])]}
CONFLICT = {"requirement_ids": ["FR-1", "FR-2"], "reason": "Instant approval contradicts mandatory manual review.",
            "suggested_fix": "Approve instantly only below a risk threshold; review the rest."}


def test_endpoint_combines_rule_and_llm_flags(monkeypatch):
    fake, calls = scripted({"conflicts": [CONFLICT]})
    monkeypatch.setattr(qa, "generate_text", fake)
    r = client.post("/agents/quality", json=REQS)
    assert r.status_code == 200 and len(calls) == 1
    d = r.json()
    conflict = [f for f in d["flags"] if f["type"] == "conflict"]
    assert conflict == [{**CONFLICT, "type": "conflict", "detected_by": "llm"}]
    assert d["counts"]["conflict"] == 1
    assert sum(d["counts"].values()) == len(d["flags"])
    assert all(set(f) == {"requirement_ids", "type", "reason", "suggested_fix", "detected_by"} for f in d["flags"])
    assert "p95 < 1 s" in calls[0]  # acceptance criteria reach the prompt


def test_no_conflicts_is_a_valid_answer(monkeypatch):
    fake, _ = scripted(NO_CONFLICTS)
    monkeypatch.setattr(qa, "generate_text", fake)
    assert client.post("/agents/quality", json=REQS).json()["counts"]["conflict"] == 0


def test_single_requirement_skips_the_llm(monkeypatch):
    monkeypatch.setattr(qa, "generate_text", lambda *a, **k: pytest.fail("no LLM call expected"))
    d = qa.analyze_quality("X", [req("FR-1", "The system shall be easy to use.")], [])
    assert d["counts"] == {"ambiguity": 1, "untestable": 0, "duplicate": 0, "conflict": 0}


@pytest.mark.parametrize("bad", [
    {"conflicts": [dict(CONFLICT, requirement_ids=["FR-1"])]},           # needs two ids
    {"conflicts": [dict(CONFLICT, requirement_ids=["FR-1", "FR-1"])]},   # same id twice
    {"conflicts": [dict(CONFLICT, requirement_ids=["FR-1", "FR-99"])]},  # unknown id
    {"conflicts": [dict(CONFLICT, suggested_fix="")]},
    {"conflicts": [dict(CONFLICT, reason="  ")]},
    "There are no conflicts.",                                            # not JSON
])
def test_invalid_conflict_output_is_retried_then_502(monkeypatch, bad):
    fake, calls = scripted(bad)
    monkeypatch.setattr(qa, "generate_text", fake)
    r = client.post("/agents/quality", json=REQS)
    assert r.status_code == 502 and len(calls) == 2
    assert r.json()["detail"].startswith("Quality check failed: model output failed validation")


def test_invalid_then_valid_conflict_output_recovers(monkeypatch):
    fake, calls = scripted({"conflicts": [dict(CONFLICT, requirement_ids=["FR-1", "FR-99"])]}, {"conflicts": [CONFLICT]})
    monkeypatch.setattr(qa, "generate_text", fake)
    assert client.post("/agents/quality", json=REQS).status_code == 200 and len(calls) == 2
    assert "FR-99" in calls[1]  # the retry tells the model what was wrong
