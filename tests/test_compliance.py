"""Compliance agent (fix 1): per-requirement retrieval, threshold filtering,
deterministic kb_relevance and dropping of unsupported citations. Offline."""
import json

import pytest

import app.agents.compliance_agent as comp
from app.agents.compliance_agent import RELEVANCE_THRESHOLD, TOP_K_PER_QUERY

FR = [{"id": "FR-1", "requirement": "The system shall process card payments."},
      {"id": "FR-2", "requirement": "The system shall show a menu."}]
NFR = [{"id": "NFR-1", "requirement": "The system shall encrypt personal data."},
       {"id": "NFR-2", "requirement": "The system shall respond within 2 seconds."}]


def chunk(cid, dist, text=None):
    return {"id": cid, "source": cid.rsplit("-", 1)[0], "text": text or f"text of {cid}", "distance": dist}


def install(monkeypatch, per_req, llm_reply):
    """per_req: dict requirement text -> chunk list. Returns a dict recording calls."""
    seen = {}

    def fake_retrieve_many(queries, top_k=3):
        seen["queries"], seen["top_k"] = queries, top_k
        return [per_req.get(q, []) for q in queries]

    def fake_llm(prompt, temperature=0.3):
        seen["prompt"] = prompt
        return json.dumps(llm_reply)

    monkeypatch.setattr(comp, "retrieve_many", fake_retrieve_many)
    monkeypatch.setattr(comp, "generate_text", fake_llm)
    return seen


REPLY = {"risk_level": "High", "kb_relevance": "relevant", "summary": "s", "considerations": [
    {"related_requirement_ids": ["FR-1"], "note": "tokenise cards", "source": "payment_security"},
]}


def test_one_short_query_per_requirement(monkeypatch):
    seen = install(monkeypatch, {}, REPLY)
    comp.analyze_compliance("Food Delivery", FR, NFR)
    assert seen["queries"] == [r["requirement"] for r in FR + NFR]
    assert seen["top_k"] == TOP_K_PER_QUERY


def test_only_chunks_under_threshold_reach_the_prompt(monkeypatch):
    per_req = {FR[0]["requirement"]: [chunk("payment_security-1", 0.40, "STRONG-TEXT"),
                                      chunk("healthcare_privacy-2", 0.90, "WEAK-TEXT")]}
    seen = install(monkeypatch, per_req, REPLY)
    d = comp.analyze_compliance("Food Delivery", FR, NFR)
    assert "STRONG-TEXT" in seen["prompt"] and "WEAK-TEXT" not in seen["prompt"]
    assert d["sources_used"] == ["payment_security"]
    assert d["sources_consulted"] == ["healthcare_privacy", "payment_security"]
    assert [c["id"] for c in d["retrieved_chunks"]] == ["payment_security-1"]


def test_distance_equal_to_threshold_is_not_relevant(monkeypatch):
    per_req = {FR[0]["requirement"]: [chunk("payment_security-1", RELEVANCE_THRESHOLD)]}
    install(monkeypatch, per_req, REPLY)
    d = comp.analyze_compliance("X", FR, NFR)
    assert d["kb_relevance"] == "not_relevant" and d["sources_used"] == []


def test_not_relevant_forces_empty_considerations_even_if_llm_cites(monkeypatch):
    per_req = {r["requirement"]: [chunk("payment_security-1", 0.80)] for r in FR + NFR}
    install(monkeypatch, per_req, REPLY)  # LLM claims "relevant" and cites a source
    d = comp.analyze_compliance("Warehouse", FR, NFR)
    assert d["kb_relevance"] == "not_relevant"
    assert d["considerations"] == []
    assert len(d["dropped_considerations"]) == 1


@pytest.mark.parametrize("matched, expected", [
    (0, "not_relevant"), (1, "partially_relevant"), (2, "relevant"), (4, "relevant"),
])
def test_kb_relevance_is_derived_from_requirement_coverage(monkeypatch, matched, expected):
    reqs = FR + NFR
    per_req = {r["requirement"]: [chunk("data_protection-0", 0.45 if i < matched else 0.85)]
               for i, r in enumerate(reqs)}
    install(monkeypatch, per_req, dict(REPLY, kb_relevance="not_relevant", considerations=[]))
    assert comp.analyze_compliance("X", FR, NFR)["kb_relevance"] == expected


def test_considerations_citing_unretrieved_or_weak_sources_are_dropped(monkeypatch):
    per_req = {FR[0]["requirement"]: [chunk("payment_security-1", 0.40)],
               NFR[0]["requirement"]: [chunk("data_protection-0", 0.50)],
               FR[1]["requirement"]: [chunk("healthcare_privacy-3", 0.80)]}
    reply = dict(REPLY, considerations=[
        {"related_requirement_ids": ["FR-1"], "note": "ok", "source": "payment_security"},
        {"related_requirement_ids": ["NFR-1"], "note": "ok", "source": "data_protection"},
        {"related_requirement_ids": ["FR-2"], "note": "weak", "source": "healthcare_privacy"},
        {"related_requirement_ids": ["FR-1"], "note": "never retrieved", "source": "kyc_aml"},
    ])
    install(monkeypatch, per_req, reply)
    d = comp.analyze_compliance("X", FR, NFR)
    assert [c["source"] for c in d["considerations"]] == ["payment_security", "data_protection"]
    assert sorted(c["source"] for c in d["dropped_considerations"]) == ["healthcare_privacy", "kyc_aml"]
    assert d["kb_relevance"] == "relevant"


def test_merge_keeps_closest_distance_and_matching_requirements(monkeypatch):
    per_req = {FR[0]["requirement"]: [chunk("payment_security-1", 0.55)],
               NFR[0]["requirement"]: [chunk("payment_security-1", 0.42)],
               NFR[1]["requirement"]: [chunk("payment_security-1", 0.90)]}
    install(monkeypatch, per_req, REPLY)
    d = comp.analyze_compliance("X", FR, NFR)
    (c,) = d["retrieved_chunks"]
    assert c["distance"] == 0.42 and c["matched_requirement_ids"] == ["FR-1", "NFR-1"]


# Nearest chunks measured live for the railway requirements (docs/RAG_TUNING.md).
RAILWAY_NEAREST = [("payment_security-0", 0.793), ("payment_security-1", 0.806),
                   ("payment_security-2", 0.835), ("kyc_aml-2", 0.626),
                   ("general_security_baseline-3", 0.674), ("payment_security-3", 0.771),
                   ("general_security_baseline-4", 0.750)]


def test_railway_measured_distances_give_not_relevant_and_no_citations(monkeypatch):
    reqs = [{"id": f"R-{i}", "requirement": f"railway requirement {i}"} for i in range(7)]
    per_req = {r["requirement"]: [chunk(cid, dist)] for r, (cid, dist) in zip(reqs, RAILWAY_NEAREST)}
    install(monkeypatch, per_req, dict(REPLY, considerations=[
        {"related_requirement_ids": ["R-3"], "note": "AML logging", "source": "kyc_aml"}]))
    d = comp.analyze_compliance("Railway Signalling", reqs[:4], reqs[4:])
    assert d["kb_relevance"] == "not_relevant"
    assert d["considerations"] == [] and d["sources_used"] == []


# --- Only domain-specific sources count toward kb_relevance ------------------

from app.agents.compliance_agent import GENERIC_SOURCES, SCOPE_DOMAIN, SCOPE_GENERIC
from app.rag.retriever import known_sources

BASELINE_CITE = {"related_requirement_ids": ["NFR-1"], "note": "uptime targets as duty of care",
                 "source": "general_security_baseline"}


def test_generic_sources_exist_in_the_knowledge_base():
    assert GENERIC_SOURCES and GENERIC_SOURCES <= set(known_sources())


def test_baseline_only_match_is_not_relevant_but_citation_is_kept_and_labelled(monkeypatch):
    # Live railway runs 1 and 3: only an availability requirement matched, and
    # only the domain-agnostic baseline (0.565).
    per_req = {NFR[0]["requirement"]: [chunk("general_security_baseline-3", 0.565)],
               FR[0]["requirement"]: [chunk("kyc_aml-2", 0.626)]}
    seen = install(monkeypatch, per_req, dict(REPLY, considerations=[BASELINE_CITE]))
    d = comp.analyze_compliance("Railway Signalling", FR, NFR)
    assert d["kb_relevance"] == "not_relevant"
    (cited,) = d["considerations"]
    assert {k: cited[k] for k in (*BASELINE_CITE, "scope")} == dict(BASELINE_CITE, scope=SCOPE_GENERIC)
    assert d["dropped_considerations"] == []
    assert d["sources_used"] == ["general_security_baseline"]
    assert [c["scope"] for c in d["retrieved_chunks"]] == [SCOPE_GENERIC]
    assert "scope: generic baseline" in seen["prompt"]


def test_baseline_matching_every_requirement_is_still_not_relevant(monkeypatch):
    per_req = {r["requirement"]: [chunk("general_security_baseline-1", 0.45)] for r in FR + NFR}
    install(monkeypatch, per_req, dict(REPLY, considerations=[BASELINE_CITE]))
    assert comp.analyze_compliance("Anything", FR, NFR)["kb_relevance"] == "not_relevant"


def test_baseline_matches_do_not_lift_partial_coverage_to_relevant(monkeypatch):
    # 1 of 4 requirements has a domain-specific match; the baseline matches the
    # other three. Counting the baseline would make this "relevant".
    per_req = {FR[0]["requirement"]: [chunk("payment_security-1", 0.40)],
               **{r["requirement"]: [chunk("general_security_baseline-1", 0.50)] for r in (FR[1], NFR[0], NFR[1])}}
    install(monkeypatch, per_req, REPLY)
    d = comp.analyze_compliance("Food Delivery", FR, NFR)
    assert d["kb_relevance"] == "partially_relevant"
    assert {c["source"]: c["scope"] for c in d["retrieved_chunks"]} == {
        "payment_security": SCOPE_DOMAIN, "general_security_baseline": SCOPE_GENERIC}


def test_not_relevant_drops_domain_citations_but_keeps_baseline(monkeypatch):
    per_req = {NFR[0]["requirement"]: [chunk("general_security_baseline-3", 0.50),
                                       chunk("payment_security-1", 0.80)]}
    reply = dict(REPLY, considerations=[BASELINE_CITE, {
        "related_requirement_ids": ["FR-1"], "note": "weak", "source": "payment_security"}])
    install(monkeypatch, per_req, reply)
    d = comp.analyze_compliance("X", FR, NFR)
    assert [c["source"] for c in d["considerations"]] == ["general_security_baseline"]
    assert [c["source"] for c in d["dropped_considerations"]] == ["payment_security"]


@pytest.mark.parametrize("domain", ["Railway Signalling", "Social Media Platform", "Digital Loan Origination"])
def test_rule_depends_on_sources_not_on_the_domain_name(monkeypatch, domain):
    per_req = {NFR[0]["requirement"]: [chunk("general_security_baseline-3", 0.50)]}
    install(monkeypatch, per_req, dict(REPLY, considerations=[BASELINE_CITE]))
    d = comp.analyze_compliance(domain, FR, NFR)
    assert d["kb_relevance"] == "not_relevant" and len(d["considerations"]) == 1


# Distances measured live for the loan / banking queries in the tuning set
# (docs/RAG_TUNING.md, requirement-only format).
LOAN_REQS = [
    ({"id": "FR-1", "requirement": "verify identity documents and proof of address"}, ("kyc_aml-3", 0.432)),
    ({"id": "FR-2", "requirement": "give declined applicants the reason for the decision"}, ("fair_lending-2", 0.461)),
    ({"id": "FR-3", "requirement": "disclose interest rates and fees before commitment"}, ("fair_lending-1", 0.339)),
    ({"id": "NFR-1", "requirement": "require multi-factor authentication for login"}, ("general_security_baseline-1", 0.638)),
    ({"id": "NFR-2", "requirement": "encrypt applicant data in transit and at rest"}, ("general_security_baseline-1", 0.540)),
]


def test_loan_processing_scenario_is_still_relevant(monkeypatch):
    reqs = [r for r, _ in LOAN_REQS]
    per_req = {r["requirement"]: [chunk(cid, dist)] for r, (cid, dist) in LOAN_REQS}
    reply = dict(REPLY, considerations=[
        {"related_requirement_ids": ["FR-1"], "note": "KYC verification", "source": "kyc_aml"},
        {"related_requirement_ids": ["FR-2", "FR-3"], "note": "explainable decisions, disclosure", "source": "fair_lending"},
        {"related_requirement_ids": ["NFR-2"], "note": "encryption baseline", "source": "general_security_baseline"},
    ])
    install(monkeypatch, per_req, reply)
    d = comp.analyze_compliance("Digital Loan Origination", reqs[:3], reqs[3:])
    assert d["kb_relevance"] == "relevant"  # 3 of 5 requirements matched domain-specific sources
    assert {c["source"]: c["scope"] for c in d["considerations"]} == {
        "kyc_aml": SCOPE_DOMAIN, "fair_lending": SCOPE_DOMAIN, "general_security_baseline": SCOPE_GENERIC}
    assert d["dropped_considerations"] == []


def test_prompt_cap_does_not_hide_a_domain_match_from_kb_relevance(monkeypatch):
    # More generic chunks than MAX_CONTEXT_CHUNKS, all closer than the one
    # domain-specific match: relevance must still see the domain match.
    reqs = [{"id": f"FR-{i}", "requirement": f"req {i}"} for i in range(1, 11)]
    per_req = {r["requirement"]: [chunk(f"general_security_baseline-{i}", 0.30 + i / 100)]
               for i, r in enumerate(reqs[:9])}
    per_req[reqs[9]["requirement"]] = [chunk("data_protection-1", 0.60)]
    seen = install(monkeypatch, per_req, dict(REPLY, considerations=[]))
    d = comp.analyze_compliance("X", reqs[:5], reqs[5:])
    assert seen["prompt"].count("[Source:") == comp.MAX_CONTEXT_CHUNKS
    assert len(d["retrieved_chunks"]) == 10  # every chunk under the threshold is reported
    assert d["kb_relevance"] == "partially_relevant"


# --- Phase 2: only sources that apply to the scenario's domain count ---------

from app.rag.kb_metadata import DOMAIN_TAGS, applies, classify_domains, load_metadata, source_info

PAYROLL = [{"id": "FR-1", "requirement": "The system shall keep an audit trail of all payroll overrides."},
           {"id": "FR-2", "requirement": "The system shall generate monthly payslips."},
           {"id": "NFR-1", "requirement": "The system shall encrypt employee salary data at rest."},
           {"id": "NFR-2", "requirement": "The system shall finish a payroll run within 4 hours."}]


def test_metadata_covers_every_kb_document_with_the_section_5_fields():
    meta = load_metadata()
    assert set(meta) == set(known_sources())
    for name, m in meta.items():
        assert m["source"] and m["jurisdiction"] and m["version"] and m["effective_date"], name
        assert m["applies_to"] and all(t == "any" or t in DOMAIN_TAGS for t in m["applies_to"]), name
    assert source_info("kyc_aml")["jurisdiction"].startswith("India")


def test_financial_domain_tags_are_quoted_from_section_1():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "docs" / "PROBLEM_STATEMENT.md").read_text(encoding="utf-8")
    for tag in DOMAIN_TAGS:
        if tag not in ("Healthcare", "Card or online payments"):
            assert f"- {tag}" in text, tag


@pytest.mark.parametrize("texts, expected", [
    (["Enterprise Payroll Processing System", "calculate salaries and payslips"], []),
    (["Social Media Platform", "log user interaction events"], []),
    (["Digital Loan Origination", "verify KYC documents"], ["Loan origination and credit assessment", "Customer onboarding and KYC"]),
    (["Food Delivery Platform", "The system shall process secure digital payments."], ["Card or online payments"]),
    (["Telehealth", "encrypt patient consultation video"], ["Healthcare"]),
])
def test_domain_classification(texts, expected):
    assert classify_domains(texts) == expected


def test_applicability_rules():
    assert applies("kyc_aml", ["Loan origination and credit assessment"])
    assert not applies("kyc_aml", [])                       # payroll, social media, railway
    assert not applies("kyc_aml", ["Card or online payments"])  # a shop taking card payments is not a KYC institution
    assert applies("payment_security", ["Card or online payments"])
    assert applies("data_protection", []) and applies("general_security_baseline", [])
    assert not applies("not_a_source", ["Healthcare"])


def test_payroll_audit_trail_matching_the_aml_paragraph_does_not_count(monkeypatch):
    # Known weakness from the live runs: payroll's audit-trail requirement matched
    # the KYC/AML transaction-logging paragraph at about 0.60.
    per_req = {PAYROLL[0]["requirement"]: [chunk("kyc_aml-2", 0.5996)],
               PAYROLL[2]["requirement"]: [chunk("data_protection-1", 0.55)]}
    reply = dict(REPLY, considerations=[
        {"related_requirement_ids": ["FR-1"], "note": "AML logging", "source": "kyc_aml"},
        {"related_requirement_ids": ["NFR-1"], "note": "encrypt personal data", "source": "data_protection"}])
    seen = install(monkeypatch, per_req, reply)
    d = comp.analyze_compliance("Enterprise Payroll Processing System", PAYROLL[:2], PAYROLL[2:])
    assert d["domain_tags"] == []
    assert d["kb_relevance"] == "partially_relevant"  # 1 of 4 (counting KYC/AML would make it 2 of 4: "relevant")
    assert d["sources_used"] == ["data_protection"]
    assert [c["source"] for c in d["considerations"]] == ["data_protection"]
    assert [c["source"] for c in d["dropped_considerations"]] == ["kyc_aml"]
    assert "kyc_aml" not in seen["prompt"]
    assert {c["source"]: c["applies_to_domain"] for c in d["retrieved_chunks"]} == {"kyc_aml": False, "data_protection": True}


def test_same_match_counts_when_the_domain_is_lending(monkeypatch):
    per_req = {PAYROLL[0]["requirement"]: [chunk("kyc_aml-2", 0.5996)],
               PAYROLL[2]["requirement"]: [chunk("data_protection-1", 0.55)]}
    install(monkeypatch, per_req, dict(REPLY, considerations=[]))
    d = comp.analyze_compliance("Digital Loan Origination", PAYROLL[:2], PAYROLL[2:])
    assert d["kb_relevance"] == "relevant" and d["sources_used"] == ["data_protection", "kyc_aml"]


def test_scenario_text_contributes_to_domain_tags(monkeypatch):
    install(monkeypatch, {}, dict(REPLY, considerations=[]))
    d = comp.analyze_compliance("Back-office system", PAYROLL[:2], PAYROLL[2:],
                                scenario="Staff process loan applications and credit checks.")
    assert d["domain_tags"] == ["Loan origination and credit assessment"]
