"""
Agent 1: Requirement Identifier

Takes a free-text scenario (plus any interview answers collected so far) and
produces structured requirement records following docs/PROBLEM_STATEMENT.md
section 8: statement, multi-label categories, source stakeholder, the exact
source text it derives from, business justification, priority, dependencies,
assumptions, acceptance criteria and risk level. Type, confidence, approval
status and applicable regulations are set by code (see app/schemas.py).

Designed to stay consistent in structure regardless of domain - that's what
the few-shot example and strict schema instructions in the prompt are for.
"""
import json

from app.gemini_client import generate_text
from app.schemas import RequirementsOutput, generate_validated
from app.vocabulary import REQUIREMENT_CATEGORIES, STAKEHOLDER_ROLES

PROMPT_TEMPLATE = """You are a senior business analyst specializing in software requirements engineering.

Given a scenario description (and any stakeholder interview answers), extract a clear, structured list of software requirements.

Rules:
- Produce BOTH functional requirements (FR) and non-functional requirements (NFR).
- Each requirement must be a single, clear, testable sentence (no vague statements like "should be good" or "fast").
- Functional requirements describe WHAT the system does (features, behaviors, user actions).
- Non-functional requirements describe HOW WELL the system does it (performance, security, usability, scalability, reliability, compliance, etc).
- Assign each requirement a priority and a risk_level: "High", "Medium", or "Low". Priority = how critical it is to the system's core purpose. Risk level = the business, technical or compliance risk if it is wrong or missing. Do not mark everything High: reserve High for what the system cannot go live without.
- categories: one or more of {categories}. A requirement may belong to several.
- source_stakeholder: exactly one of {roles}.
- source_text: copy, word for word, the phrase (at least 2 words) from the scenario or an interview answer that this requirement comes from. If the requirement is not stated there and you are inferring it, use "" and list what you assumed in that requirement's "assumptions".
- Any number you add that the input does not state (response times, uptime, limits) is an assumption: list it in that requirement's "assumptions".
- business_justification: one sentence on why the business needs it.
- acceptance_criteria: 1-3 checkable statements, with measurable values where possible.
- dependencies: ids of other requirements in your list that this one depends on ([] if none).
- Extract at least 4 functional and 3 non-functional requirements. Do NOT invent unrelated features not implied by the input.
- Do not name laws, regulations or standards unless the input names them; compliance mapping is done separately.
- Respond with ONLY valid JSON, no markdown fences, no commentary, matching exactly the schema in the example.

Example:
Scenario: "An app where users can order food from local restaurants and track delivery."
Output:
{example}

Now do the same for this input.
Scenario: "{scenario}"
{answers}
Respond with ONLY the JSON object.
"""

EXAMPLE = {
    "domain_inferred": "Food Delivery Platform",
    "assumptions": ["Payment is handled digitally, not cash-only"],
    "functional_requirements": [
        {"id": "FR-1", "requirement": "The system shall allow users to order food from a selected local restaurant.",
         "priority": "High", "risk_level": "Medium", "categories": ["Functional", "Business"],
         "source_stakeholder": "Customers and end users", "source_text": "order food from local restaurants",
         "business_justification": "Placing orders is the core revenue-generating action.",
         "acceptance_criteria": ["A user can add menu items to a cart and submit the order",
                                 "The restaurant receives the submitted order"],
         "dependencies": [], "assumptions": []},
        {"id": "FR-2", "requirement": "The system shall show the user the current status and location of their delivery.",
         "priority": "Medium", "risk_level": "Low", "categories": ["Functional", "Usability"],
         "source_stakeholder": "Customers and end users", "source_text": "track delivery",
         "business_justification": "Tracking reduces support contacts about late orders.",
         "acceptance_criteria": ["Status changes from 'preparing' to 'out for delivery' to 'delivered' are shown"],
         "dependencies": ["FR-1"], "assumptions": []},
    ],
    "non_functional_requirements": [
        {"id": "NFR-1", "requirement": "The system shall encrypt all payment data in transit and at rest.",
         "priority": "High", "risk_level": "High", "categories": ["Security", "Privacy"],
         "source_stakeholder": "Information-security teams", "source_text": "",
         "business_justification": "Payment data exposure would cause financial and reputational damage.",
         "acceptance_criteria": ["Payment API traffic uses TLS 1.2 or later", "Stored payment data is encrypted with AES-256"],
         "dependencies": ["FR-1"], "assumptions": ["Payment is handled digitally, not cash-only"]},
        {"id": "NFR-2", "requirement": "The system shall update a delivery's location at least every 30 seconds.",
         "priority": "Medium", "risk_level": "Low", "categories": ["Performance"],
         "source_stakeholder": "Customers and end users", "source_text": "",
         "business_justification": "Stale locations make tracking useless.",
         "acceptance_criteria": ["95% of location updates reach the app within 30 seconds"],
         "dependencies": ["FR-2"], "assumptions": ["30-second freshness is acceptable to users"]},
    ],
}


def _format_answers(answers: list[dict] | None) -> str:
    if not answers:
        return ""
    lines = ["Interview answers so far:"]
    for a in answers:
        lines.append(f"- [{a['role']}] Q: {a['question']}\n  A: {a['answer']}")
    return "\n".join(lines) + "\n"


def extract_requirements(scenario: str, answers: list[dict] | None = None) -> dict:
    """
    Returns a dict with keys: domain_inferred, assumptions,
    functional_requirements, non_functional_requirements, validated against
    RequirementsOutput. Every source_text must be an exact quote from the
    scenario or an answer (an answer's quote also fixes source_stakeholder to
    the person who gave it). Raises LLMOutputError if the output is still
    invalid after one retry.
    """
    prompt = PROMPT_TEMPLATE.format(
        categories=", ".join(REQUIREMENT_CATEGORIES),
        roles=", ".join(STAKEHOLDER_ROLES),
        example=json.dumps(EXAMPLE, indent=1),
        scenario=scenario.strip(),
        answers=_format_answers(answers),
    )
    source_texts = [{"text": scenario, "role": None}] + [
        {"text": a["answer"], "role": a["role"]} for a in (answers or [])
    ]
    result = generate_validated(
        prompt, RequirementsOutput, generate=generate_text,
        temperature=0.1,  # low: steadier requirement lists run to run
        context={"source_texts": source_texts},
    )
    return result.model_dump()
