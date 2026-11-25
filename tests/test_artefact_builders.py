"""Phase 4 step 1: artefacts built by code (no LLM). Offline."""
from app.artefacts import (
    assumptions_register, build_code_artefacts, compliance_control_matrix, open_issues, source_statements,
    traceability_matrix,
)
from tests.artefact_fixtures import GENERATED, MAPPING, QUALITY, REQUIREMENTS, RISKS, SDLC, WORKFLOW


def test_traceability_matrix_links_requirement_to_statement_compliance_risks_and_artefacts():
    rtm = traceability_matrix(REQUIREMENTS, MAPPING, RISKS, GENERATED, WORKFLOW)
    assert [r["requirement_id"] for r in rtm] == ["FR-1", "FR-2", "NFR-1"]
    fr1, fr2, nfr1 = rtm
    assert fr1["source"] == {"requirement_id": "FR-1", "stakeholder": "Compliance and legal officers",
                             "statement": "upload identity and address documents for KYC verification", "assumptions": []}
    assert fr1["compliance_sources"] == [{"source": "kyc_aml", "status": "satisfied", "confidence": "High"}]
    assert fr1["risk_ids"] == ["RISK-2"] and fr1["user_stories_ids"] == ["US-1"] and fr1["use_cases_ids"] == []
    assert fr2["risk_ids"] == ["RISK-1"] and fr2["use_cases_ids"] == ["UC-1"]
    assert fr2["workflow_items"] == ["Requirements analysis: Decline-reason tests"]
    assert nfr1["compliance_sources"] == [] and nfr1["source"]["statement"] == ""
    assert nfr1["source"]["assumptions"] == ["5 seconds is acceptable | to applicants"]
    assert all(r["approval_status"] == "proposed" for r in rtm)


def test_rtm_excludes_not_applicable_and_unassessed_matches():
    mapping = {"requirements": [{"requirement_id": "FR-1", "needs_human_review": True, "review_reasons": ["x"], "mappings": [
        dict(MAPPING["requirements"][0]["mappings"][0], status="not_applicable"),
        dict(MAPPING["requirements"][0]["mappings"][0], source="data_protection", status="not_assessed")]}]}
    rtm = traceability_matrix(REQUIREMENTS, mapping, None, None, None)
    assert rtm[0]["compliance_sources"] == []


def test_compliance_control_matrix_has_one_row_per_requirement_and_source():
    rows = compliance_control_matrix(MAPPING)
    assert [(r["requirement_id"], r["source"], r["status"]) for r in rows] == [("FR-1", "kyc_aml", "satisfied"),
                                                                                ("FR-2", "fair_lending", "gap")]
    assert rows[1]["compliance_gap"] == "No appeal route." and rows[1]["needs_human_review"] is True
    assert rows[0]["jurisdiction"] == "India" and rows[0]["checkpoints"] == ["Compliance sign-off"]
    assert compliance_control_matrix(None) == []


def test_assumptions_register_merges_identical_texts_and_lists_dependencies():
    reg = assumptions_register(REQUIREMENTS)
    assert reg["assumptions"][0] == {"id": "A-1", "text": "Credit bureau API is available", "scope": "project",
                                     "requirement_ids": ["FR-2"]}
    assert reg["assumptions"][1]["requirement_ids"] == ["NFR-1"] and len(reg["assumptions"]) == 2
    assert reg["dependencies"] == [{"requirement_id": "FR-2", "depends_on": ["FR-1"]},
                                   {"requirement_id": "NFR-1", "depends_on": ["FR-1", "FR-2"]}]


def test_open_issues_cover_quality_review_approvals_and_unverified_items():
    issues = open_issues(REQUIREMENTS, QUALITY, MAPPING, SDLC, GENERATED, WORKFLOW)
    kinds = [(i["type"], i["origin"]) for i in issues]
    assert ("conflict", "quality check (llm)") in kinds
    assert ("compliance review", "compliance mapping") in kinds
    assert [i for i in issues if i["type"] == "pending approval" and i["origin"] == "requirement records"][0]["requirement_ids"] \
        == ["FR-1", "FR-2", "NFR-1"]
    assert any("V-Model awaits approval by Project manager, Architect, Security team, Compliance officer" in i["description"]
               for i in issues)
    assert any(i["type"] == "close call" and "Waterfall" in i["description"] for i in issues)
    unverified = [i for i in issues if i["type"] == "unverified"]
    assert [i["origin"] for i in unverified] == ["requirement records", "artefact generation", "workflow generator"]
    assert unverified[0]["requirement_ids"] == ["NFR-1"]
    assert [i["id"] for i in issues] == [f"ISSUE-{n}" for n in range(1, len(issues) + 1)]


def test_open_issues_with_nothing_pending():
    approved = {**REQUIREMENTS, "functional_requirements": [dict(r, approval_status="approved", confidence="High")
                                                            for r in REQUIREMENTS["functional_requirements"]],
                "non_functional_requirements": []}
    assert open_issues(approved, None, None, None, None, None) == []


def test_source_statements_follow_requirement_ids_and_skip_unknown():
    out = source_statements(["FR-2", "FR-9"], REQUIREMENTS)
    assert out == [{"requirement_id": "FR-2", "stakeholder": "Customers and end users",
                    "statement": "declined applicants must be told the reason", "assumptions": ["Credit bureau API is available"]}]


def test_build_code_artefacts_returns_all_four():
    out = build_code_artefacts(REQUIREMENTS, MAPPING, RISKS, QUALITY, SDLC, WORKFLOW, GENERATED)
    assert set(out) == {"traceability_matrix", "compliance_control_matrix", "assumptions_register", "open_issues"}
