"""
Agent 1: Requirement Identifier

Takes a free-text scenario/module/feature description and produces a
structured list of functional and non-functional requirements.

Designed to stay consistent in structure regardless of domain (banking,
healthcare, e-commerce, whatever the evaluator types in) - that's what
the few-shot example and strict schema instructions in the prompt are for.
"""
from app.gemini_client import generate_text
from app.schemas import RequirementsOutput, generate_validated

PROMPT_TEMPLATE = """You are a senior business analyst specializing in software requirements engineering.

Given a scenario description, extract a clear, structured list of software requirements.

Rules:
- Produce BOTH functional requirements (FR) and non-functional requirements (NFR).
- Each requirement must be a single, clear, testable sentence (no vague statements like "should be good").
- Functional requirements describe WHAT the system does (features, behaviors, user actions).
- Non-functional requirements describe HOW WELL the system does it (performance, security, usability, scalability, reliability, compliance, etc).
- Assign each requirement a priority: "High", "Medium", or "Low", based on how critical it is to the system's core purpose and any safety/compliance/financial implications.
- Extract at least 4 functional and 3 non-functional requirements. If the scenario is short or vague, use reasonable, industry-standard assumptions for that type of system and note them.
- Do NOT invent unrelated features not implied by the scenario.
- Respond with ONLY valid JSON, no markdown fences, no commentary, matching exactly this schema:

{{
  "domain_inferred": "short label for the type of system, e.g. 'Online Banking - Loan Origination'",
  "assumptions": ["any assumptions you made, if the scenario was vague - empty list if none"],
  "functional_requirements": [
    {{"id": "FR-1", "requirement": "...", "priority": "High"}},
    {{"id": "FR-2", "requirement": "...", "priority": "Medium"}}
  ],
  "non_functional_requirements": [
    {{"id": "NFR-1", "requirement": "...", "priority": "High"}}
  ]
}}

Example:
Scenario: "An app where users can order food from local restaurants and track delivery."
Output:
{{
  "domain_inferred": "Food Delivery Platform",
  "assumptions": ["Assumed payment is handled digitally, not cash-only"],
  "functional_requirements": [
    {{"id": "FR-1", "requirement": "The system shall allow users to browse restaurants and menus by location.", "priority": "High"}},
    {{"id": "FR-2", "requirement": "The system shall allow users to place an order and select delivery address.", "priority": "High"}},
    {{"id": "FR-3", "requirement": "The system shall allow users to track the real-time status of their delivery.", "priority": "Medium"}},
    {{"id": "FR-4", "requirement": "The system shall allow users to pay for orders through integrated payment methods.", "priority": "High"}}
  ],
  "non_functional_requirements": [
    {{"id": "NFR-1", "requirement": "The system shall process order placement within 2 seconds under normal load.", "priority": "Medium"}},
    {{"id": "NFR-2", "requirement": "The system shall encrypt all payment information in transit and at rest.", "priority": "High"}},
    {{"id": "NFR-3", "requirement": "The system shall be available 99.5% of the time, given the delivery-hours-critical nature of the service.", "priority": "Medium"}}
  ]
}}

Now do the same for this scenario:
Scenario: "{scenario}"

Respond with ONLY the JSON object.
"""


def extract_requirements(scenario: str) -> dict:
    """
    Returns a dict with keys: domain_inferred, assumptions,
    functional_requirements, non_functional_requirements, validated against
    RequirementsOutput (FR-n / NFR-n ids, unique, priority High/Medium/Low).
    Raises LLMOutputError if the output is still invalid after one retry.
    """
    prompt = PROMPT_TEMPLATE.format(scenario=scenario.strip())
    result = generate_validated(
        prompt, RequirementsOutput, generate=generate_text,
        temperature=0.1,  # low: steadier requirement lists run to run
    )
    return result.model_dump()
