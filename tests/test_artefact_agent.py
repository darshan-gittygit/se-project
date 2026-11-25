"""Phase 4 step 2: LLM-generated artefacts (stories, use cases, workflows, data, interfaces). Offline."""
import copy
import json

import pytest

import app.agents.artefact_agent as aa
from tests.artefact_fixtures import REQUIREMENTS


def story(ids=("FR-1",), **kw):
    return {"role": "loan applicant", "goal": "to upload my documents", "benefit": "my application is processed",
            "acceptance_criteria": ["Given a PDF, when I upload it, then it is stored"], "requirement_ids": list(ids), **kw}


def use_case(ids=("FR-2",), **kw):
    return {"name": "Decline an application", "primary_actor": "Credit officer", "preconditions": ["Score computed"],
            "main_flow": ["Officer declines", "System sends the reason"], "alternative_flows": [],
            "postconditions": ["Applicant informed"], "requirement_ids": list(ids), **kw}


GOOD = {
    "user_stories": [story(), story(("FR-2",), goal="to know why I was declined")],
    "use_cases": [use_case()],
    "process_workflows": [{"name": "Loan application", "steps": [{"actor": "Applicant", "action": "Submits"},
                                                                 {"actor": "System", "action": "Scores"}],
                           "requirement_ids": ["FR-1", "NFR-1"]}],
    "data_requirements": [{"entity": "Applicant document", "description": "Uploaded KYC file",
                           "attributes": ["type", "file"], "retention": "", "requirement_ids": ["FR-1"]}],
    "interface_requirements": [{"name": "Credit bureau API", "interface_type": "Software interface",
                                "description": "Fetch bureau data", "requirement_ids": ["NFR-1"]}],
}


def scripted(*replies):
    calls = []

    def fake(prompt, temperature=0.3):
        calls.append(prompt)
        r = replies[min(len(calls), len(replies)) - 1]
        return r if isinstance(r, str) else json.dumps(r)
    return fake, calls


def run(monkeypatch, *replies, requirements=REQUIREMENTS):
    fake, calls = scripted(*replies)
    monkeypatch.setattr(aa, "generate_text", fake)
    return aa.generate_artefacts(requirements, "A loan system."), calls


def test_one_call_with_code_assigned_ids_story_text_and_source_links(monkeypatch):
    out, calls = run(monkeypatch, GOOD)
    assert len(calls) == 1 and out["generation"]["llm_calls"] == 1 and out["generation"]["split"] is False
    assert [s["id"] for s in out["user_stories"]] == ["US-1", "US-2"]
    assert out["user_stories"][0]["story"] == ("As a loan applicant, I want to upload my documents, "
                                               "so that my application is processed.")
    assert out["user_stories"][0]["source_statements"] == [{
        "requirement_id": "FR-1", "stakeholder": "Compliance and legal officers",
        "statement": "upload identity and address documents for KYC verification", "assumptions": []}]
    assert [out[k][0]["id"] for k in ("use_cases", "process_workflows", "data_requirements", "interface_requirements")] \
        == ["UC-1", "PW-1", "DR-1", "IR-1"]
    assert [s["requirement_id"] for s in out["process_workflows"][0]["source_statements"]] == ["FR-1", "NFR-1"]
    p = calls[0]
    assert "FR-1 [FR; Functional, Regulatory] Compliance and legal officers:" in p
    assert '"interface_requirements"' in p and "Software interface" in p


def test_story_article_follows_the_role(monkeypatch):
    out, _ = run(monkeypatch, dict(GOOD, user_stories=[story(role="applicant"), story(("FR-2",), role="credit officer")]))
    assert out["user_stories"][0]["story"].startswith("As an applicant,")
    assert out["user_stories"][1]["story"].startswith("As a credit officer,")


def test_empty_optional_sections_are_allowed(monkeypatch):
    out, _ = run(monkeypatch, dict(GOOD, process_workflows=[], data_requirements=[], interface_requirements=[]))
    assert out["process_workflows"] == [] and out["interface_requirements"] == []


BAD = [
    dict(GOOD, priority_notes="x"),                                                         # invented top-level key
    dict(GOOD, user_stories=[story(estimate=3), story(("FR-2",))]),                         # invented item field
    dict(GOOD, user_stories=[story(("FR-9",)), story(("FR-2",))]),                          # unknown id
    dict(GOOD, user_stories=[story(())]),                                                   # no link
    dict(GOOD, user_stories=[story()]),                                                     # FR-2 has no story
    dict(GOOD, user_stories=[story(acceptance_criteria=[" "]), story(("FR-2",))]),          # blank criterion
    dict(GOOD, use_cases=[]),                                                               # no use cases
    dict(GOOD, use_cases=[use_case(main_flow=["only one step"])]),
    dict(GOOD, interface_requirements=[dict(GOOD["interface_requirements"][0], interface_type="REST")]),
    dict(GOOD, process_workflows=[dict(GOOD["process_workflows"][0], steps=[{"actor": "A", "action": "x", "time": 1},
                                                                              {"actor": "B", "action": "y"}])]),
    {k: v for k, v in GOOD.items() if k != "user_stories"},                                 # missing section
]


@pytest.mark.parametrize("bad", BAD)
def test_invalid_output_is_retried_then_raises(monkeypatch, bad):
    fake, calls = scripted(bad)
    monkeypatch.setattr(aa, "generate_text", fake)
    with pytest.raises(Exception, match="failed validation after 2 attempts"):
        aa.generate_artefacts(REQUIREMENTS)
    assert len(calls) == 2


def test_invalid_then_valid_recovers(monkeypatch):
    out, calls = run(monkeypatch, BAD[0], GOOD)
    assert len(calls) == 2 and len(out["user_stories"]) == 2 and "rejected" in calls[1]


def _many_requirements(n):
    reqs = copy.deepcopy(REQUIREMENTS)
    base = reqs["non_functional_requirements"][0]
    reqs["non_functional_requirements"] = [dict(base, id=f"NFR-{i}") for i in range(1, n - 1)]
    return reqs


def test_large_inputs_are_split_into_two_calls(monkeypatch):
    reqs = _many_requirements(aa.SPLIT_THRESHOLD + 1)
    part_a = {"user_stories": GOOD["user_stories"], "use_cases": GOOD["use_cases"]}
    part_b = {k: GOOD[k] for k in ("process_workflows", "data_requirements", "interface_requirements")}
    out, calls = run(monkeypatch, part_a, part_b, requirements=reqs)
    assert len(calls) == 2 and out["generation"] == {
        "llm_calls": 2, "split": True,
        "note": f"More than {aa.SPLIT_THRESHOLD} requirements: generated in two calls to keep each response small."}
    assert '"user_stories", "use_cases"' in calls[0] and '"process_workflows"' not in calls[0]
    assert '"process_workflows", "data_requirements", "interface_requirements"' in calls[1]
    assert out["interface_requirements"][0]["id"] == "IR-1"


def test_split_parts_reject_each_others_sections(monkeypatch):
    reqs = _many_requirements(aa.SPLIT_THRESHOLD + 1)
    fake, calls = scripted(GOOD)  # the full set is not valid for part A (extra keys)
    monkeypatch.setattr(aa, "generate_text", fake)
    with pytest.raises(Exception, match="failed validation"):
        aa.generate_artefacts(reqs)
    assert len(calls) == 2


def test_threshold_boundary_stays_one_call(monkeypatch):
    reqs = _many_requirements(aa.SPLIT_THRESHOLD)
    out, calls = run(monkeypatch, GOOD, requirements=reqs)
    assert len(calls) == 1 and out["generation"]["split"] is False
