"""Phase 2 step 2: per-requirement compliance mapping, graded confidence and human-review escalation. Offline."""
import json

import pytest
from fastapi.testclient import TestClient

import app.agents.compliance_agent as comp
from app.agents.compliance_agent import attach_regulations
from app.agents.compliance_mapping import NOTICE, grade_confidence
from app.main import app

client = TestClient(app)
DOMAIN = "Digital Loan Origination"
KYC_TEXT = ("A digital loan origination or account-opening system should therefore typically include: identity "
            "document capture and verification, address verification, a risk-scoring step to classify customer "
            "risk level, and an audit trail of verification decisions.")
FAIR_TEXT = ("Borrowers who are declined should generally receive a reason for the decision, not just a "
             "rejection notice.")
DP_TEXT = "Protect the data with reasonable technical safeguards (encryption in transit and at rest, access controls)."

FR = [{"id": "FR-1", "requirement": "The system shall verify applicant identity documents for the loan.", "risk_level": "High",
       "categories": ["Functional", "Regulatory"]},
      {"id": "FR-2", "requirement": "The system shall tell declined loan applicants the reason.", "risk_level": "Medium",
       "categories": ["Functional"]}]
NFR = [{"id": "NFR-1", "requirement": "The system shall encrypt applicant data at rest.", "risk_level": "Medium",
        "categories": ["Security"]}]


def chunk(cid, dist, text):
    return {"id": cid, "source": cid.rsplit("-", 1)[0], "text": text, "distance": dist}


def finding(ids, source, status="satisfied", evidence="", gap=None, note="control", **kw):
    return {"related_requirement_ids": ids, "note": note, "source": source, "status": status,
            "evidence": evidence, "compliance_gap": gap, **kw}


def run(monkeypatch, considerations, per_req=None, risk="High"):
    per_req = per_req or {
        FR[0]["requirement"]: [chunk("kyc_aml-4", 0.43, KYC_TEXT)],
        FR[1]["requirement"]: [chunk("fair_lending-3", 0.52, FAIR_TEXT)],
        NFR[0]["requirement"]: [chunk("data_protection-1", 0.58, DP_TEXT)],
    }
    calls = []

    def fake(prompt, temperature=0.3):
        calls.append(prompt)
        return json.dumps({"risk_level": risk, "summary": "s", "considerations": considerations})
    monkeypatch.setattr(comp, "retrieve_many", lambda qs, top_k=3: [per_req.get(q, []) for q in qs])
    monkeypatch.setattr(comp, "generate_text", fake)
    return comp.analyze_compliance(DOMAIN, FR, NFR), calls


def entry(d, rid):
    return next(e for e in d["compliance_mapping"]["requirements"] if e["requirement_id"] == rid)


GOOD = [
    finding(["FR-1"], "kyc_aml", evidence="identity document capture and verification, address verification"),
    finding(["FR-2"], "fair_lending", evidence="should generally receive a reason for the decision"),
    finding(["NFR-1"], "data_protection", evidence="encryption in transit and at rest, access controls"),
]


def test_every_requirement_gets_an_entry_with_code_set_metadata(monkeypatch):
    d, _ = run(monkeypatch, GOOD)
    m = d["compliance_mapping"]
    assert m["notice"] == NOTICE and [e["requirement_id"] for e in m["requirements"]] == ["FR-1", "FR-2", "NFR-1"]
    (item,) = entry(d, "FR-1")["mappings"]
    assert item == {
        "source": "kyc_aml", "regulation": "Know Your Customer (KYC) and Anti-Money Laundering (AML) Principles",
        "scope": "domain-specific", "jurisdiction": "India; principles common to most jurisdictions",
        "version": "1", "effective_date": "2026-11-25", "chunk_id": "kyc_aml-4", "distance": 0.43,
        "status": "satisfied", "required_control": "control",
        "evidence": "identity document capture and verification, address verification", "evidence_verified": True,
        "compliance_gap": None, "checkpoints": [], "retention_and_reporting": [], "confidence": "High",
    }


def test_confidence_is_graded_from_distance_and_evidence(monkeypatch):
    d, _ = run(monkeypatch, GOOD)
    assert [entry(d, r)["mappings"][0]["confidence"] for r in ("FR-1", "FR-2", "NFR-1")] == ["High", "Medium", "Low"]
    assert entry(d, "FR-1")["needs_human_review"] is False
    assert entry(d, "NFR-1")["review_reasons"] == ["data_protection: low-confidence match (distance 0.58)"]
    assert d["compliance_mapping"]["review_count"] == 1


@pytest.mark.parametrize("distance, verified, expected", [
    (0.43, True, "High"), (0.50, True, "Medium"), (0.549, True, "Medium"), (0.55, True, "Low"),
    (0.43, False, "Low"), (None, True, "Low"),
])
def test_grade_confidence_bands(distance, verified, expected):
    assert grade_confidence(distance, verified) == expected


@pytest.mark.parametrize("evidence", [
    "approves every loan within one minute",          # not in the source
    "identity document",                               # too short to trace
])
def test_unverifiable_evidence_is_low_confidence_and_reviewed(monkeypatch, evidence):
    d, _ = run(monkeypatch, [finding(["FR-1"], "kyc_aml", evidence=evidence)] + GOOD[1:])
    item = entry(d, "FR-1")["mappings"][0]
    assert item["evidence_verified"] is False and item["confidence"] == "Low"
    assert "evidence quote not found in the source text" in entry(d, "FR-1")["review_reasons"][0]


def test_evidence_check_ignores_case_and_punctuation(monkeypatch):
    d, _ = run(monkeypatch, [finding(["FR-1"], "kyc_aml", evidence="Identity-document capture AND verification")] + GOOD[1:])
    assert entry(d, "FR-1")["mappings"][0]["evidence_verified"] is True


def test_unassessed_match_is_listed_and_reviewed(monkeypatch):
    d, _ = run(monkeypatch, GOOD[:1] + GOOD[2:])  # nothing about FR-2 / fair_lending
    (item,) = entry(d, "FR-2")["mappings"]
    assert item["status"] == "not_assessed" and item["source"] == "fair_lending"
    assert entry(d, "FR-2")["needs_human_review"]
    assert "did not assess" in entry(d, "FR-2")["review_reasons"][0]


def test_not_applicable_is_kept_for_review_but_not_cited(monkeypatch):
    d, _ = run(monkeypatch, GOOD[:1] + [finding(["FR-2"], "fair_lending", status="not_applicable")] + GOOD[2:])
    assert entry(d, "FR-2")["mappings"][0]["status"] == "not_applicable"
    assert "ambiguous match" in entry(d, "FR-2")["review_reasons"][0]
    assert "fair_lending" not in [c["source"] for c in d["considerations"]]
    reqs = {"functional_requirements": [dict(r) for r in FR], "non_functional_requirements": [dict(r) for r in NFR]}
    attach_regulations(reqs, d)
    assert reqs["functional_requirements"][1]["applicable_regulations"] == []


def test_citation_without_a_retrieval_match_for_that_requirement_is_reviewed(monkeypatch):
    # kyc_aml was retrieved for FR-1 only; the model also cites it for NFR-1
    d, _ = run(monkeypatch, [finding(["FR-1", "NFR-1"], "kyc_aml", evidence=GOOD[0]["evidence"])] + GOOD[1:2])
    item = next(i for i in entry(d, "NFR-1")["mappings"] if i["source"] == "kyc_aml")
    assert item["distance"] is None and item["confidence"] == "Low"
    assert "kyc_aml: cited without a retrieval match for this requirement" in entry(d, "NFR-1")["review_reasons"]


def test_gap_on_a_high_risk_requirement_needs_compliance_approval(monkeypatch):
    d, _ = run(monkeypatch, [finding(["FR-1"], "kyc_aml", status="gap", gap="No address verification.",
                                     evidence=GOOD[0]["evidence"])] + GOOD[1:])
    item = entry(d, "FR-1")["mappings"][0]
    assert item["status"] == "gap" and item["compliance_gap"] == "No address verification."
    assert entry(d, "FR-1")["review_reasons"] == [
        "kyc_aml: compliance gap on a high-risk requirement; the interpretation needs compliance or legal approval"]


def test_gap_on_a_medium_risk_requirement_with_good_evidence_is_not_escalated(monkeypatch):
    d, _ = run(monkeypatch, GOOD[:1] + [finding(["FR-2"], "fair_lending", status="gap", gap="No reason text.",
                                                evidence=GOOD[1]["evidence"])] + GOOD[2:])
    assert entry(d, "FR-2")["needs_human_review"] is False


def test_regulatory_requirement_without_domain_support_is_reviewed(monkeypatch):
    per_req = {FR[0]["requirement"]: [chunk("general_security_baseline-1", 0.45, DP_TEXT)]}
    d, _ = run(monkeypatch, [], per_req=per_req)
    assert "regulatory requirement with no domain-specific knowledge-base support" in entry(d, "FR-1")["review_reasons"]
    assert entry(d, "FR-2") == {"requirement_id": "FR-2", "mappings": [], "needs_human_review": False, "review_reasons": []}


def test_checkpoints_and_retention_pass_through(monkeypatch):
    d, _ = run(monkeypatch, [finding(["FR-1"], "kyc_aml", evidence=GOOD[0]["evidence"],
                                     checkpoints=["Compliance sign-off before go-live"],
                                     retention_and_reporting=["Retain verification records"])] + GOOD[1:])
    item = entry(d, "FR-1")["mappings"][0]
    assert item["checkpoints"] == ["Compliance sign-off before go-live"]
    assert item["retention_and_reporting"] == ["Retain verification records"]


BODY = {"domain": DOMAIN, "functional_requirements": [{"id": r["id"], "requirement": r["requirement"]} for r in FR],
        "non_functional_requirements": [{"id": r["id"], "requirement": r["requirement"]} for r in NFR]}


@pytest.mark.parametrize("bad", [
    finding(["FR-1"], "kyc_aml", status="gap"),                                   # gap without description
    finding(["FR-1"], "kyc_aml", status="satisfied", gap="something missing"),   # gap text but satisfied
    finding(["FR-1"], "kyc_aml", status="partially"),                            # unknown status
    finding(["FR-1"], "kyc_aml", checkpoints=["  "]),                            # blank item
])
def test_invalid_mapping_fields_are_retried_then_502(monkeypatch, bad):
    calls = []
    monkeypatch.setattr(comp, "retrieve_many", lambda qs, top_k=3: [[] for _ in qs])
    monkeypatch.setattr(comp, "generate_text", lambda p, temperature=0.3: calls.append(p) or json.dumps(
        {"risk_level": "High", "summary": "s", "considerations": [bad]}))
    r = client.post("/agents/compliance", json=BODY)
    assert r.status_code == 502 and len(calls) == 2
    assert r.json()["detail"].startswith("Agent 3 (Compliance) failed: model output failed validation")


def test_compliance_endpoint_returns_the_mapping(monkeypatch):
    per_req = {FR[0]["requirement"]: [chunk("kyc_aml-4", 0.43, KYC_TEXT)]}
    monkeypatch.setattr(comp, "retrieve_many", lambda qs, top_k=3: [per_req.get(q, []) for q in qs])
    monkeypatch.setattr(comp, "generate_text", lambda p, temperature=0.3: json.dumps(
        {"risk_level": "High", "summary": "s", "considerations": GOOD[:1]}))
    d = client.post("/agents/compliance", json=BODY).json()
    assert d["compliance_mapping"]["requirements"][0]["mappings"][0]["confidence"] == "High"
    assert {"risk_level", "kb_relevance", "considerations", "summary", "sources_consulted", "sources_used"} <= set(d)
