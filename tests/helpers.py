"""Shared test data: a complete requirement record as Agent 1's LLM must return it."""


def record(rid, requirement, priority="High", **overrides):
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
