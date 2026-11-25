"""Phase 5A: roles, approval actions, versions, regeneration, baseline, persistence. Offline."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.agents.compliance_agent as comp_mod
import app.agents.requirement_agent as req_mod
import app.agents.sdlc_agent as sdlc_mod
from app import approvals, roles
from app.main import app
from app.runs import get_run, new_run_id, save_run
from tests.artefact_fixtures import pipeline_result

client = TestClient(app)
BA, PO, SEC, COMP, PM, ARCH, AUD = ("business analyst", "product owner", "security officer", "compliance officer",
                                    "project manager", "architect", "auditor")


def H(role):
    return {"X-Role": role}


@pytest.fixture
def run_id():
    rid = new_run_id()
    result = pipeline_result()
    # The scenario must contain the requirements' source_text quotes: edits and regenerations are re-checked against it.
    result["scenario"] = ("A digital loan origination system for a bank. Applicants upload identity and address documents "
                          "for KYC verification, and declined applicants must be told the reason.")
    result["requirements"]["non_functional_requirements"].append({
        **result["requirements"]["non_functional_requirements"][0], "id": "NFR-2",
        "requirement": "The system shall encrypt applicant documents at rest.", "categories": ["Security"],
        "risk_level": "High", "dependencies": [], "source_text": "", "assumptions": ["Documents are stored"]})
    save_run(rid, result, BA)
    return rid


def act(rid, item_type, item_id, action, role, comment="ok", content=None):
    return client.post(f"/runs/{rid}/approvals", headers=H(role),
                       json={"item_type": item_type, "item_id": item_id, "action": action, "comment": comment,
                             "content": content})


def approve_everything(rid):
    for i in ("FR-1", "FR-2", "NFR-1"):
        assert act(rid, "requirement", i, "accept", PO).status_code == 200
    assert act(rid, "requirement", "NFR-2", "accept", SEC).status_code == 200
    for i in ("FR-1/kyc_aml", "FR-2/fair_lending"):
        assert act(rid, "compliance", i, "accept", COMP).status_code == 200
    for r in (PM, ARCH, SEC, COMP):
        assert act(rid, "sdlc", "sdlc", "accept", r).status_code == 200


# --- registration ------------------------------------------------------------------

def test_run_registers_every_mandatory_item_as_proposed(run_id):
    d = client.get(f"/runs/{run_id}").json()
    keys = [(i["item_type"], i["item_id"], i["version"], i["status"]) for i in d["items"]]
    assert keys == [("requirement", "FR-1", 1, "proposed"), ("requirement", "FR-2", 1, "proposed"),
                    ("requirement", "NFR-1", 1, "proposed"), ("requirement", "NFR-2", 1, "proposed"),
                    ("compliance", "FR-1/kyc_aml", 1, "proposed"), ("compliance", "FR-2/fair_lending", 1, "proposed"),
                    ("sdlc", "sdlc", 1, "proposed")]
    assert [i["mandatory"] for i in d["items"]] == [True] * 6 + [False]   # the SDLC selection does not block the baseline
    assert len(d["blockers"]) == 6
    assert d["baseline"]["status"] == "open" and d["kb_version"].startswith("kb-")


# --- roles and permissions ---------------------------------------------------------------

def test_role_header_is_normalised_and_unknown_roles_are_rejected():
    assert roles.parse_role("  Product_Owner ") == PO and roles.parse_role(None) == roles.UNSPECIFIED
    r = client.post("/agents/requirements", headers=H("ceo"), json={"scenario": "A payroll system"})
    assert r.status_code == 400 and "Unknown role" in r.json()["detail"]


@pytest.mark.parametrize("item_type, item_id, wrong, right", [
    ("requirement", "FR-1", ARCH, PO),            # ordinary requirement: business analyst or product owner
    ("requirement", "NFR-2", PO, SEC),            # high-risk security requirement: security officer
    ("compliance", "FR-1/kyc_aml", BA, COMP),     # regulatory interpretation: compliance officer
    ("sdlc", "sdlc", BA, PM),                     # SDLC selection: PM, architect, security, compliance
])
def test_permission_table(run_id, item_type, item_id, wrong, right):
    r = act(run_id, item_type, item_id, "accept", wrong)
    assert r.status_code == 403 and "may not" in r.json()["detail"]
    assert act(run_id, item_type, item_id, "accept", right).status_code == 200


def test_auditor_is_read_only(run_id):
    assert act(run_id, "requirement", "FR-1", "accept", AUD).status_code == 403
    assert client.post("/agents/pipeline", headers=H(AUD), json={"scenario": "A payroll system"}).status_code == 403
    assert client.post(f"/runs/{run_id}/baseline/freeze", headers=H(AUD)).status_code == 403
    assert client.get(f"/runs/{run_id}", headers=H(AUD)).status_code == 200
    assert client.get(f"/agents/srs/{run_id}", headers=H(AUD)).status_code == 200


@pytest.mark.parametrize("value, expected", [(None, False), ("", False), ("0", False), ("1", True), ("TRUE", True), ("on", True)])
def test_require_role_flag_parsing(monkeypatch, value, expected):
    from app import config
    if value is None:
        monkeypatch.delenv("REQUIRE_ROLE", raising=False)
    else:
        monkeypatch.setenv("REQUIRE_ROLE", value)
    assert config.require_role() is expected


def test_require_role_rejects_requests_without_a_header(run_id, monkeypatch):
    monkeypatch.setenv("REQUIRE_ROLE", "1")
    for r in (client.post("/agents/requirements", json={"scenario": "A payroll system"}),
              client.get(f"/runs/{run_id}"), client.get(f"/agents/srs/{run_id}"), client.get("/audit"),
              act(run_id, "requirement", "FR-1", "accept", "")):
        assert r.status_code == 400 and "X-Role header is required" in r.json()["detail"]
    assert client.get(f"/runs/{run_id}", headers=H(AUD)).status_code == 200
    assert client.get("/health").status_code == 200   # health and the UI page do not need a role


def test_without_require_role_missing_headers_still_work(run_id, monkeypatch):
    monkeypatch.delenv("REQUIRE_ROLE", raising=False)
    assert client.get(f"/runs/{run_id}").status_code == 200


def test_approval_actions_need_a_role(run_id):
    r = client.post(f"/runs/{run_id}/approvals", json={"item_type": "requirement", "item_id": "FR-1", "action": "accept"})
    assert r.status_code == 403 and "X-Role" in r.json()["detail"]


# --- accept / reject / modify --------------------------------------------------------------

def test_accept_then_no_second_decision_on_the_same_version(run_id):
    d = act(run_id, "requirement", "FR-1", "accept", PO, comment="").json()
    assert d["status"] == "approved" and d["version"] == 1
    assert act(run_id, "requirement", "FR-1", "accept", BA).status_code == 409
    assert act(run_id, "requirement", "FR-1", "reject", BA, "no").status_code == 409


def test_reject_needs_a_comment_and_blocks_accept(run_id):
    assert act(run_id, "requirement", "FR-2", "reject", PO, comment=" ").status_code == 400
    assert act(run_id, "requirement", "FR-2", "reject", PO, comment="Too vague").json()["status"] == "rejected"
    assert act(run_id, "requirement", "FR-2", "accept", PO).status_code == 409


def test_modify_creates_a_new_version_that_needs_approval(run_id):
    d = act(run_id, "requirement", "FR-2", "modify", BA, "Make it measurable",
            {"requirement": "The system shall show declined applicants the reason within 1 minute.",
             "acceptance_criteria": ["The reason is shown within 1 minute"]}).json()
    assert (d["version"], d["status"]) == (2, "modified")
    assert d["history"] == [{"version": 1, "status": "superseded"}, {"version": 2, "status": "modified"}]
    assert d["content"]["requirement"].endswith("within 1 minute.") and d["content"]["type"] == "FR"
    assert act(run_id, "requirement", "FR-2", "accept", PO).json()["status"] == "approved"


def test_changing_an_approved_requirement_needs_approval_again(run_id):
    act(run_id, "requirement", "FR-1", "accept", PO)
    d = act(run_id, "requirement", "FR-1", "modify", PO, "Add PAN check", {"priority": "Medium"}).json()
    assert (d["version"], d["status"]) == (2, "modified")
    assert d["history"][0] == {"version": 1, "status": "superseded"}


@pytest.mark.parametrize("content, why", [
    ({"id": "FR-9"}, "cannot be changed"),
    ({"priority": "Urgent"}, "not valid"),
    ({"source_text": "approves every loan instantly", "assumptions": []}, "not an exact quote"),
    ({"dependencies": ["FR-77"]}, "dependencies"),
    (None, "needs the changed fields"),
])
def test_invalid_modifications_are_rejected(run_id, content, why):
    r = act(run_id, "requirement", "FR-1", "modify", PO, "change", content)
    assert r.status_code == 400 and why in r.json()["detail"]


def test_compliance_interpretation_can_be_modified_with_schema_rules(run_id):
    bad = act(run_id, "compliance", "FR-2/fair_lending", "modify", COMP, "x", {"status": "gap", "compliance_gap": None})
    assert bad.status_code == 400
    d = act(run_id, "compliance", "FR-2/fair_lending", "modify", COMP, "Appeal route exists in policy",
            {"status": "satisfied", "compliance_gap": None}).json()
    assert d["status"] == "modified" and d["content"]["status"] == "satisfied" and d["content"]["reviewed"] is True


# --- SDLC: all four approvers ------------------------------------------------------------------

def test_sdlc_needs_all_four_approvers(run_id):
    d = act(run_id, "sdlc", "sdlc", "accept", PM).json()
    assert d["status"] == "proposed" and d["accepted_by"] == [PM]
    assert d["awaiting"] == sorted({ARCH, SEC, COMP})
    assert act(run_id, "sdlc", "sdlc", "accept", PM).status_code == 409   # same role twice
    for r in (ARCH, SEC):
        act(run_id, "sdlc", "sdlc", "accept", r)
    d = act(run_id, "sdlc", "sdlc", "accept", COMP).json()
    assert d["status"] == "approved" and d["awaiting"] == []


def test_one_sdlc_reject_rejects_it(run_id):
    act(run_id, "sdlc", "sdlc", "accept", PM)
    assert act(run_id, "sdlc", "sdlc", "reject", SEC, "Security model unclear").json()["status"] == "rejected"


def test_sdlc_override_is_recorded_and_shown(run_id):
    assert act(run_id, "sdlc", "sdlc", "modify", ARCH, "x", {"recommended_model": "Scrum"}).status_code == 400
    d = act(run_id, "sdlc", "sdlc", "modify", ARCH, "Team runs sprints", {"recommended_model": "Agile-V"}).json()
    assert d["content"]["human_override"] is True and d["status"] == "modified"
    res = client.get(f"/runs/{run_id}/result").json()
    assert res["sdlc_recommendation"]["human_override"] == "Agile-V"


# --- regeneration ------------------------------------------------------------------------------

def _regen_requirement_reply(prompts):
    rec = pipeline_result()["requirements"]["functional_requirements"][1]
    rec = {k: v for k, v in rec.items() if k not in ("type", "confidence", "approval_status", "applicable_regulations")}

    def fake(prompt, temperature=0.3):
        prompts.append(prompt)
        return json.dumps({"requirement": dict(rec, requirement="The system shall state the decline reason in plain language.")})
    return fake


def test_regenerate_requirement_with_comment_and_cap(run_id, monkeypatch):
    prompts = []
    monkeypatch.setattr(req_mod, "generate_text", _regen_requirement_reply(prompts))
    d = act(run_id, "requirement", "FR-2", "regenerate", PO, "Use plain language").json()
    assert (d["version"], d["status"], d["regenerations"]) == (2, "proposed", 1)
    assert d["content"]["requirement"] == "The system shall state the decline reason in plain language."
    assert "Use plain language" in prompts[0] and "keep the id FR-2" in prompts[0]
    for n in (2, 3):
        assert act(run_id, "requirement", "FR-2", "regenerate", PO, f"again {n}").json()["regenerations"] == n
    r = act(run_id, "requirement", "FR-2", "regenerate", PO, "once more")
    assert r.status_code == 409 and f"{approvals.MAX_REGENERATIONS} times" in r.json()["detail"]


def test_regenerate_requirement_that_breaks_the_rules_fails_cleanly(run_id, monkeypatch):
    monkeypatch.setattr(req_mod, "generate_text", lambda p, temperature=0.3: json.dumps({"requirement": {"id": "FR-9"}}))
    r = act(run_id, "requirement", "FR-2", "regenerate", PO, "fix")
    assert r.status_code == 502 and r.json()["detail"].startswith("Regeneration failed: model output failed validation")
    item = client.get(f"/runs/{run_id}").json()["items"][1]
    assert (item["item_id"], item["version"], item["regenerations"]) == ("FR-2", 1, 0)  # nothing was stored


def test_regenerate_compliance_reruns_the_stage_for_that_requirement(run_id, monkeypatch):
    prompts = []
    monkeypatch.setattr(comp_mod, "retrieve_many", lambda qs, top_k=3: [[
        {"id": "fair_lending-3", "source": "fair_lending", "distance": 0.45,
         "text": "Borrowers who are declined should generally receive a reason for the decision, not just a rejection notice."}]
        for _ in qs])
    monkeypatch.setattr(comp_mod, "generate_text", lambda p, temperature=0.3: prompts.append(p) or json.dumps({
        "risk_level": "Medium", "summary": "s", "considerations": [{
            "related_requirement_ids": ["FR-2"], "note": "Give a reason", "source": "fair_lending", "status": "satisfied",
            "evidence": "declined should generally receive a reason for the decision"}]}))
    d = act(run_id, "compliance", "FR-2/fair_lending", "regenerate", COMP, "Reassess with the appeal process in mind").json()
    assert d["version"] == 2 and d["content"]["status"] == "satisfied" and d["content"]["confidence"] == "High"
    assert "Reassess with the appeal process in mind" in prompts[0]
    assert prompts[0].count("FR-") >= 1 and "NFR-1:" not in prompts[0]  # only FR-2 was re-analysed


def test_regenerate_sdlc_reruns_trait_rating_with_the_comment(run_id, monkeypatch):
    prompts = []
    traits = {k: {"score": 3, "evidence": "e", "reason": "r"} for k in sdlc_mod.TRAIT_KEYS}
    just = {"requirement_characteristics": "a", "project_characteristics": "b", "engineering_justification": "c",
            "key_tradeoffs": "d"}
    monkeypatch.setattr(sdlc_mod, "generate_text", lambda p, temperature=0: prompts.append(p) or json.dumps(
        traits if "rating project characteristics" in p else just))
    d = act(run_id, "sdlc", "sdlc", "regenerate", PM, "Volatility is higher than rated").json()
    assert d["version"] == 2 and d["status"] == "proposed" and d["content"]["traits"]["volatility"] == 3
    assert "Volatility is higher than rated" in prompts[0]


# --- baseline ----------------------------------------------------------------------------------

def test_baseline_freeze_is_blocked_until_every_mandatory_item_is_approved(run_id):
    act(run_id, "requirement", "FR-1", "accept", PO)
    r = client.post(f"/runs/{run_id}/baseline/freeze", headers=H(PO))
    assert r.status_code == 409
    blockers = r.json()["detail"]["blockers"]
    assert {(b["item_type"], b["item_id"]) for b in blockers} == {
        ("requirement", "FR-2"), ("requirement", "NFR-1"), ("requirement", "NFR-2"),
        ("compliance", "FR-1/kyc_aml"), ("compliance", "FR-2/fair_lending")}


def test_baseline_freezes_without_the_sdlc_selection_which_stays_pending(run_id):
    for i in ("FR-1", "FR-2", "NFR-1"):
        act(run_id, "requirement", i, "accept", PO)
    act(run_id, "requirement", "NFR-2", "accept", SEC)
    for i in ("FR-1/kyc_aml", "FR-2/fair_lending"):
        act(run_id, "compliance", i, "accept", COMP)
    act(run_id, "sdlc", "sdlc", "accept", PM)
    d = client.post(f"/runs/{run_id}/baseline/freeze", headers=H(PO)).json()
    assert d["baseline_status"] == "frozen"
    s = client.get(f"/runs/{run_id}").json()
    sdlc = next(i for i in s["items"] if i["item_type"] == "sdlc")
    assert sdlc["status"] == "proposed" and sdlc["awaiting"] == sorted({ARCH, SEC, COMP})
    table = client.get(f"/agents/srs/{run_id}").text.split("## 17. Approval status")[1]
    assert "| SDLC model: V-Model | pending |" in table and "| Requirement baseline | frozen |" in table


def test_baseline_freeze_roles_and_reopen_on_change(run_id):
    approve_everything(run_id)
    assert client.post(f"/runs/{run_id}/baseline/freeze", headers=H(ARCH)).status_code == 403
    d = client.post(f"/runs/{run_id}/baseline/freeze", headers=H(PO)).json()
    assert d["baseline_status"] == "frozen" and len(d["items"]) == 7
    assert client.post(f"/runs/{run_id}/baseline/freeze", headers=H(BA)).status_code == 409   # already frozen
    act(run_id, "requirement", "FR-1", "modify", BA, "Post-baseline change", {"priority": "Medium"})
    s = client.get(f"/runs/{run_id}").json()
    assert s["baseline"]["status"] == "open" and [b["item_id"] for b in s["blockers"]] == ["FR-1"]


def test_srs_reflects_current_approval_state(run_id):
    approve_everything(run_id)
    client.post(f"/runs/{run_id}/baseline/freeze", headers=H(PO))
    md = client.get(f"/agents/srs/{run_id}").text
    assert "Baseline frozen on" in md and "by the product owner" in md
    table = md.split("## 17. Approval status")[1]
    assert "| Requirement FR-1 | approved |" in table and "| SDLC model: V-Model | approved |" in table
    assert "| Regulatory interpretations (compliance mapping) | approved |" in table
    assert "| Requirement baseline | frozen |" in table


def test_unknown_run_and_item_are_404(run_id):
    assert client.get("/runs/nope").status_code == 404
    assert act("nope", "requirement", "FR-1", "accept", PO).status_code == 404
    assert act(run_id, "requirement", "FR-99", "accept", PO).status_code == 404


# --- persistence across processes ----------------------------------------------------------------

def test_stored_run_and_decisions_survive_a_process_restart(run_id):
    act(run_id, "requirement", "FR-1", "accept", PO)
    code = ("import os, sys, json; sys.path.insert(0, os.getcwd()); os.environ.setdefault('GEMINI_API_KEY', 'x');"
            "from app.runs import get_run, current_items;"
            f"run = get_run('{run_id}'); items = {{i['item_id']: i['status'] for i in current_items('{run_id}')}};"
            "print(json.dumps({'domain': run['result']['requirements']['domain_inferred'], 'FR-1': items['FR-1']}))")
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120,
                         env={**os.environ})
    assert out.returncode == 0, out.stderr[-2000:]
    assert json.loads(out.stdout.strip().splitlines()[-1]) == {"domain": "Digital Loan Origination", "FR-1": "approved"}
