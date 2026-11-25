"""A small, complete pipeline result used by the artefact and SRS tests."""
import copy

REQUIREMENTS = {
    "domain_inferred": "Digital Loan Origination",
    "assumptions": ["Credit bureau API is available"],
    "functional_requirements": [
        {"id": "FR-1", "requirement": "The system shall verify applicant identity documents.", "priority": "High",
         "type": "FR", "categories": ["Functional", "Regulatory"], "source_stakeholder": "Compliance and legal officers",
         "source_text": "upload identity and address documents for KYC verification", "business_justification": "KYC is mandatory.",
         "dependencies": [], "assumptions": [], "acceptance_criteria": ["Documents are checked before approval"],
         "applicable_regulations": ["kyc_aml"], "risk_level": "High", "confidence": "High", "approval_status": "proposed"},
        {"id": "FR-2", "requirement": "The system shall tell declined applicants the reason.", "priority": "Medium",
         "type": "FR", "categories": ["Functional"], "source_stakeholder": "Customers and end users",
         "source_text": "declined applicants must be told the reason", "business_justification": "Fair lending.",
         "dependencies": ["FR-1"], "assumptions": ["Credit bureau API is available"],
         "acceptance_criteria": ["The decline notice states the reason"], "applicable_regulations": ["fair_lending"],
         "risk_level": "Medium", "confidence": "Medium", "approval_status": "proposed"},
    ],
    "non_functional_requirements": [
        {"id": "NFR-1", "requirement": "The system shall return a credit decision within 5 seconds.", "priority": "Medium",
         "type": "NFR", "categories": ["Performance"], "source_stakeholder": "Software architects and developers",
         "source_text": "", "business_justification": "Applicants abandon slow flows.", "dependencies": ["FR-1", "FR-2"],
         "assumptions": ["5 seconds is acceptable | to applicants"], "acceptance_criteria": ["p95 under 5 s"],
         "applicable_regulations": [], "risk_level": "Medium", "confidence": "Low", "approval_status": "proposed"},
    ],
}

MAPPING = {"notice": "Not a legal determination.", "review_count": 1, "requirements": [
    {"requirement_id": "FR-1", "needs_human_review": False, "review_reasons": [], "mappings": [
        {"source": "kyc_aml", "regulation": "KYC and AML Principles", "scope": "domain-specific",
         "jurisdiction": "India", "version": "1", "effective_date": "2026-11-25", "chunk_id": "kyc_aml-4",
         "distance": 0.43, "status": "satisfied", "required_control": "Verify identity documents",
         "evidence": "identity document capture and verification", "evidence_verified": True,
         "compliance_gap": None, "checkpoints": ["Compliance sign-off"], "retention_and_reporting": ["Keep records"],
         "confidence": "High"}]},
    {"requirement_id": "FR-2", "needs_human_review": True, "review_reasons": ["fair_lending: low-confidence match (distance 0.61)"],
     "mappings": [
        {"source": "fair_lending", "regulation": "Fair Lending Principles", "scope": "domain-specific",
         "jurisdiction": "India", "version": "1", "effective_date": "2026-11-25", "chunk_id": "fair_lending-3",
         "distance": 0.61, "status": "gap", "required_control": "Give a reason for every decline",
         "evidence": "declined should generally receive a reason", "evidence_verified": True,
         "compliance_gap": "No appeal route.", "checkpoints": [], "retention_and_reporting": [], "confidence": "Low"}]},
    {"requirement_id": "NFR-1", "needs_human_review": False, "review_reasons": [], "mappings": []},
]}

RISKS = {"risks": [
    {"id": "RISK-1", "rank": 1, "description": "Bureau outage blocks decisions", "category": "Technical",
     "likelihood": 3, "likelihood_reason": "r", "impact": 4, "impact_reason": "r", "score": 12, "level": "High",
     "mitigation": "Cache and retry", "linked_requirement_ids": ["FR-2", "NFR-1"]},
    {"id": "RISK-2", "rank": 2, "description": "Forged documents", "category": "Security",
     "likelihood": 2, "likelihood_reason": "r", "impact": 4, "impact_reason": "r", "score": 8, "level": "Medium",
     "mitigation": "Document forensics", "linked_requirement_ids": ["FR-1"]}],
    "scoring": "score = likelihood x impact", "summary": {}}

QUALITY = {"counts": {"ambiguity": 0, "untestable": 0, "duplicate": 0, "conflict": 1}, "flags": [
    {"requirement_ids": ["FR-2", "NFR-1"], "type": "conflict", "reason": "Bureau call vs 5 s.", "suggested_fix": "Cache.",
     "detected_by": "llm"}]}

SDLC = {
    "recommended_model": "V-Model", "is_close_call": True,
    "scored_models": [
        {"model": "V-Model", "score": 80.4, "rank": 1, "percent": "80.4%", "why_not": None, "profile": {}},
        {"model": "Waterfall", "score": 78.0, "rank": 2, "percent": "78.0%", "why_not": "Waterfall fits lower criticality.", "profile": {}}],
    "traits": {"clarity": 4}, "justification": {"requirement_characteristics": "Stable.", "project_characteristics": "Critical.",
                                               "engineering_justification": "Fits.", "key_tradeoffs": "Rigid."},
    "approval_required": {"approvers": ["Project manager", "Architect", "Security team", "Compliance officer"],
                          "status": "pending", "note": "Advisory only."},
}

WORKFLOW = {"model": "V-Model", "added_items": 1, "requirements_linked": ["FR-2"], "notice": "Base + scenario.", "phases": [
    {"order": 1, "name": "Requirements analysis",
     "activities": [{"text": "Specify requirements", "source": "base", "requirement_ids": []}],
     "responsible_roles": [{"text": "Business analyst", "source": "base", "requirement_ids": []}],
     "deliverables": [], "security_activities": [],
     "testing_requirements": [{"text": "Decline-reason tests", "source": "scenario", "requirement_ids": ["FR-2"], "reason": "r"}],
     "compliance_checkpoints": [], "approval_gates": [{"text": "Baseline approved", "source": "base", "requirement_ids": []}],
     "entry_criteria": [], "exit_criteria": [], "traceability_requirements": []}]}

GENERATED = {
    "user_stories": [{"id": "US-1", "role": "applicant", "goal": "upload my documents", "benefit": "my loan is processed",
                      "story": "As an applicant, I want to upload my documents so that my loan is processed.",
                      "acceptance_criteria": ["Given a PDF, when I upload it, then it is accepted"],
                      "requirement_ids": ["FR-1"], "source_statements": []}],
    "use_cases": [{"id": "UC-1", "name": "Decline an application", "primary_actor": "Credit officer",
                   "preconditions": ["Score computed"], "main_flow": ["Officer declines", "System sends reason"],
                   "alternative_flows": [], "postconditions": ["Applicant informed"], "requirement_ids": ["FR-2", "NFR-1"],
                   "source_statements": []}],
    "process_workflows": [], "data_requirements": [], "interface_requirements": [],
}


def pipeline_result():
    """A full /agents/pipeline-shaped result (deep copy, safe to mutate)."""
    from app.artefacts import build_code_artefacts
    code = build_code_artefacts(REQUIREMENTS, MAPPING, RISKS, QUALITY, SDLC, WORKFLOW, GENERATED)
    return copy.deepcopy({
        "scenario": "A digital loan origination system | for a bank.", "requirements": REQUIREMENTS,
        "compliance": {"risk_level": "High", "kb_relevance": "relevant", "summary": "Loans are regulated."},
        "compliance_mapping": MAPPING, "quality": QUALITY, "risk_register": RISKS,
        "sdlc_recommendation": SDLC, "sdlc_workflow": WORKFLOW,
        "artefacts": {**GENERATED, **code, "generation": {"llm_calls": 1, "split": False, "note": "Generated in one call."}},
        "meta": {"model": "test", "models_used": []},
    })
