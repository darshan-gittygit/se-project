"""Shared test data."""
import json


def record(rid, requirement, priority="High", **overrides):
    """A complete requirement record as Agent 1's LLM must return it."""
    rec = {
        "id": rid, "requirement": requirement, "priority": priority,
        "categories": ["Functional"] if rid.startswith("FR") else ["Performance"],
        "source_stakeholder": "Business analysts",
        "source_text": "",
        "business_justification": "Needed for the test.",
        "dependencies": [],
        "assumptions": ["assumed for the test"],
        "acceptance_criteria": ["It can be checked within 2 seconds"],
        "risk_level": "Medium",
    }
    rec.update(overrides)
    return rec


RISK_PROMPT_MARKER = "threat and risk register"


def risk(description, likelihood, impact, ids=("FR-1",), category="Technical"):
    return {"description": description, "category": category,
            "likelihood": {"score": likelihood, "reason": "per the input"},
            "impact": {"score": impact, "reason": "per the input"},
            "mitigation": "Add a control.", "linked_requirement_ids": list(ids)}


def risk_reply(ids=("FR-1",)):
    """A valid risk-register LLM reply linked to the given requirement ids."""
    return json.dumps({"risks": [risk("Bureau outage blocks scoring", 3, 4, ids),
                                 risk("Wrong decline reason", 2, 3, ids, "Compliance"),
                                 risk("Slow page loads", 2, 2, ids, "Business")]})


WORKFLOW_PROMPT_MARKER = "software development workflow to one project"
