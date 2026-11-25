"""
Builds app/static/sample_run.json - the "Load recorded sample" backup demo
for the UI - by running the REAL /agents/pipeline offline: model replies are
mocked with the test fixture's data (tests/artefact_fixtures.py), retrieval
returns real knowledge-base paragraphs, and a throwaway database is used.
So the file has exactly the shape the API returns, with no live model call.

    python -m scripts.make_sample_run

It contains no secrets and no real personal data: the scenario's only
"personal" values are an example.com address and the public 4111... test
card number, and both are masked before anything is stored. Replace this
file with a recorded live run when one is available (same shape).
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("GEMINI_API_KEY", "offline-sample-not-a-real-key")
os.environ["REQUORA_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="requora-sample-"), "sample.db")
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import app.agents.artefact_agent as aa  # noqa: E402
import app.agents.compliance_agent as ca  # noqa: E402
import app.agents.quality_agent as qa  # noqa: E402
import app.agents.requirement_agent as ra  # noqa: E402
import app.agents.risk_agent as rk  # noqa: E402
import app.agents.sdlc_agent as sa  # noqa: E402
import app.agents.workflow_agent as wa  # noqa: E402
from app.agents.sdlc_workflows import BASE_WORKFLOWS  # noqa: E402
from app.main import app  # noqa: E402
from tests.artefact_fixtures import QUALITY, REQUIREMENTS  # noqa: E402

OUT = ROOT / "app" / "static" / "sample_run.json"
SCENARIO = ("A digital loan origination system for a mid-sized bank. Applicants upload identity and address documents "
            "for KYC verification, and declined applicants must be told the reason. Support questions go to "
            "applicant.help@example.com; fees are charged to test card 4111 1111 1111 1111.")

LLM_FIELDS = ("id", "requirement", "priority", "categories", "source_stakeholder", "source_text", "business_justification",
              "dependencies", "assumptions", "acceptance_criteria", "risk_level")


def _req(r):
    return {k: r[k] for k in LLM_FIELDS}


NFR2 = {"id": "NFR-2", "requirement": "The system shall encrypt uploaded identity documents in transit and at rest.",
        "priority": "High", "categories": ["Security", "Privacy"], "source_stakeholder": "Information-security teams",
        "source_text": "", "business_justification": "Identity documents are sensitive personal data.",
        "dependencies": ["FR-1"], "assumptions": ["Documents are stored by the bank, not a third party"],
        "acceptance_criteria": ["TLS 1.2+ for uploads", "AES-256 at rest"], "risk_level": "High"}
REQS_REPLY = {"domain_inferred": REQUIREMENTS["domain_inferred"], "assumptions": REQUIREMENTS["assumptions"],
              "functional_requirements": [_req(r) for r in REQUIREMENTS["functional_requirements"]],
              "non_functional_requirements": [_req(r) for r in REQUIREMENTS["non_functional_requirements"]] + [NFR2]}


def _kb_chunks():
    """Real knowledge-base paragraphs, chunked the way ingest.py does."""
    chunks = {}
    for path in sorted((ROOT / "knowledge_base").glob("*.txt")):
        paras = [p.strip() for p in path.read_text(encoding="utf-8").split("\n\n") if p.strip()]
        for i, p in enumerate(paras):
            chunks[f"{path.stem}-{i}"] = {"id": f"{path.stem}-{i}", "source": path.stem, "text": p}
    return chunks


KB = _kb_chunks()
RETRIEVAL = {  # requirement text -> [(chunk id, distance)]
    "FR-1": [("kyc_aml-4", 0.43), ("kyc_aml-1", 0.57)],
    "FR-2": [("fair_lending-3", 0.61), ("fair_lending-1", 0.66)],   # Low band: shows a review flag
    "NFR-1": [("general_security_baseline-3", 0.71)],
    "NFR-2": [("data_protection-1", 0.55), ("general_security_baseline-1", 0.58), ("payment_security-0", 0.60)],
}


def fake_retrieve(queries, top_k=3):
    by_text = {r["requirement"]: r["id"] for r in REQS_REPLY["functional_requirements"] + REQS_REPLY["non_functional_requirements"]}
    return [[{**KB[cid], "distance": d} for cid, d in RETRIEVAL.get(by_text.get(q, ""), [])] for q in queries]


def _quote(chunk_id, words=8):
    return " ".join(re.sub(r"\s+", " ", KB[chunk_id]["text"]).split()[:words])


COMPLIANCE_REPLY = {"risk_level": "High", "summary": "Loan origination is regulated: identity verification, explainable "
                    "decline reasons and protection of identity documents apply.", "considerations": [
    {"related_requirement_ids": ["FR-1"], "note": "Verify identity documents and keep a record of verification decisions.",
     "source": "kyc_aml", "status": "satisfied", "evidence": _quote("kyc_aml-4", 12), "compliance_gap": None,
     "checkpoints": ["Compliance sign-off of the KYC procedure before go-live"],
     "retention_and_reporting": ["Retain verification records"]},
    {"related_requirement_ids": ["FR-2"], "note": "Give declined borrowers a reason for the decision.",
     "source": "fair_lending", "status": "gap", "evidence": _quote("fair_lending-3", 10),
     "compliance_gap": "No process for a borrower to request the reason later.", "checkpoints": [], "retention_and_reporting": []},
    {"related_requirement_ids": ["NFR-2"], "note": "Encrypt sensitive personal data in transit and at rest.",
     "source": "data_protection", "status": "satisfied", "evidence": _quote("data_protection-1", 10), "compliance_gap": None,
     "checkpoints": [], "retention_and_reporting": []},
    {"related_requirement_ids": ["NFR-2"], "note": "Baseline: encryption and access control for sensitive data.",
     "source": "general_security_baseline", "status": "satisfied", "evidence": _quote("general_security_baseline-1", 10),
     "compliance_gap": None, "checkpoints": [], "retention_and_reporting": []},
]}

TRAITS = {"clarity": 4, "volatility": 1, "complexity": 3, "criticality": 4, "risk_uncertainty": 1,
          "verification_validation": 4, "stakeholder_feedback": 2, "incremental_suitability": 3, "prototyping_need": 1,
          "security_risk": 4, "system_size": 3, "legacy_dependence": 3, "continuous_delivery": 2,
          "stakeholder_availability": 3, "budget_schedule_constraint": 3}
TRAITS_REPLY = {k: {"score": v, "evidence": "From the scenario and requirements (recorded sample).",
                    "reason": f"Rated {v} against the rubric."} for k, v in TRAITS.items()}
JUSTIFICATION = {"requirement_characteristics": "Requirements are clear and stable (clarity 4/5, volatility 1/5).",
                 "project_characteristics": "Failure has major financial and regulatory consequences (criticality 4/5) "
                                            "in a well-understood domain (risk/uncertainty 1/5).",
                 "engineering_justification": "Verification & validation intensity of 4/5 and the audit trail in FR-1 suit "
                                              "a plan-driven model with planned test levels.",
                 "key_tradeoffs": "Late changes to credit rules are costly."}


def risk(desc, cat, l, i, ids, mitigation):
    return {"description": desc, "category": cat, "likelihood": {"score": l, "reason": "Recorded sample rating."},
            "impact": {"score": i, "reason": "Recorded sample rating."}, "mitigation": mitigation,
            "linked_requirement_ids": ids}


RISKS = {"risks": [
    risk("Credit bureau outage blocks decisions within the response-time target", "Technical", 3, 4, ["FR-2", "NFR-1"],
         "Cache bureau responses and queue applications during outages"),
    risk("No route for borrowers to request a decline reason later", "Compliance", 4, 3, ["FR-2"],
         "Add a reason-request step to the decline notice"),
    risk("Forged identity documents pass verification", "Security", 2, 5, ["FR-1"], "Document forensics and liveness checks"),
    risk("Document storage leaks applicant data", "Security", 2, 4, ["NFR-2"], "Encrypt at rest and restrict access"),
    risk("Scope growth from new loan products", "Business", 2, 2, ["FR-1"], "Change control on the product list"),
]}


def workflow_reply(prompt):
    model = re.search(r"Base workflow for (.+?) \(fixed", prompt).group(1)
    phases = [p["name"] for p in BASE_WORKFLOWS[model]]
    return {"additions": [
        {"phase": phases[1], "field": "security_activities", "item": "Threat modelling of the document-upload flow",
         "requirement_ids": ["FR-1", "NFR-2"], "reason": "Identity documents are a fraud target."},
        {"phase": phases[-2], "field": "testing_requirements", "item": "Decline-reason content tests for every decline path",
         "requirement_ids": ["FR-2"], "reason": "The compliance mapping found a gap here."},
        {"phase": phases[-1], "field": "compliance_checkpoints", "item": "Compliance approval of the KYC procedure before go-live",
         "requirement_ids": ["FR-1"], "reason": "Checkpoint named by the compliance mapping."}]}


ARTEFACTS = {
    "user_stories": [
        {"role": "loan applicant", "goal": "to upload my identity and address documents", "benefit": "my KYC check can start",
         "acceptance_criteria": ["Given a PDF or image, when I upload it, then it is stored and verification starts"],
         "requirement_ids": ["FR-1"]},
        {"role": "declined applicant", "goal": "to see why my application was declined", "benefit": "I can act on it",
         "acceptance_criteria": ["Given a decline, when I open the notice, then the specific reason is shown"],
         "requirement_ids": ["FR-2"]}],
    "use_cases": [{"name": "Decline an application", "primary_actor": "Credit officer",
                   "preconditions": ["Credit score computed"], "main_flow": ["Officer reviews the score", "Officer declines",
                                                                            "System sends the decline reason"],
                   "alternative_flows": ["Score unavailable: application queued"], "postconditions": ["Applicant informed"],
                   "requirement_ids": ["FR-2", "NFR-1"]}],
    "process_workflows": [{"name": "Loan application to decision", "steps": [
        {"actor": "Applicant", "action": "Uploads identity and address documents"},
        {"actor": "System", "action": "Runs KYC verification"},
        {"actor": "System", "action": "Calculates the credit score"},
        {"actor": "Credit officer", "action": "Approves or declines with a reason"}], "requirement_ids": ["FR-1", "FR-2", "NFR-1"]}],
    "data_requirements": [{"entity": "Applicant document", "description": "Uploaded KYC document",
                           "attributes": ["document type", "file", "verification status"], "retention": "",
                           "requirement_ids": ["FR-1", "NFR-2"]}],
    "interface_requirements": [{"name": "Credit bureau API", "interface_type": "Software interface",
                                "description": "Fetch bureau data for scoring", "requirement_ids": ["NFR-1"]}],
}


def router(prompt, temperature=0.3):
    if "senior business analyst" in prompt:
        return json.dumps(REQS_REPLY)
    if "compliance and risk analyst" in prompt:
        return json.dumps(COMPLIANCE_REPLY)
    if "requirements list for CONFLICTS" in prompt:
        return json.dumps({"conflicts": [{"requirement_ids": QUALITY["flags"][0]["requirement_ids"],
                                          "reason": "Waiting for the credit bureau can exceed the 5-second decision target.",
                                          "suggested_fix": "Cache bureau data or relax the target for bureau timeouts."}]})
    if "threat and risk register" in prompt:
        return json.dumps(RISKS)
    if "rating project characteristics" in prompt:
        return json.dumps(TRAITS_REPLY)
    if "structured recommendation report" in prompt:
        return json.dumps(JUSTIFICATION)
    if "software development workflow to one project" in prompt:
        return json.dumps(workflow_reply(prompt))
    if "writing the artefacts of a Software Requirements Specification" in prompt:
        return json.dumps(ARTEFACTS)
    raise AssertionError("unexpected prompt: " + prompt[:80])


def main():
    for mod in (ra, ca, qa, rk, sa, wa, aa):
        mod.generate_text = router
    ca.retrieve_many = fake_retrieve
    r = TestClient(app).post("/agents/pipeline", headers={"X-Role": "business analyst"},
                             json={"scenario": SCENARIO, "answers": [
                                 {"role": "Risk-management teams", "question": "What limits apply?",
                                  "answer": "Loans above the bank's risk threshold need a second approver."}]})
    assert r.status_code == 200, r.text
    result = r.json()
    result["_sample"] = {"label": "Recorded sample, not a live run",
                         "note": "Generated offline by scripts/make_sample_run.py from the test fixture's data with mocked "
                                 "model replies and real knowledge-base paragraphs. Replace it with a recorded live run."}
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size // 1024} KB); recommended model {result['sdlc_recommendation']['recommended_model']}")


if __name__ == "__main__":
    main()
