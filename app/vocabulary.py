"""
Controlled vocabularies taken word for word from docs/PROBLEM_STATEMENT.md,
so schemas, prompts and interview templates all use the same labels.

- STAKEHOLDER_ROLES: section 2 "Identify stakeholders"
- INTERVIEW_TOPICS: section 7 "Design requirement-gathering conversations"
- REQUIREMENT_CATEGORIES: section 9 "Classify the requirements"
- APPROVAL_STATUSES: section 16 (users accept, reject or modify AI outputs);
  every generated requirement starts as "proposed"; older versions of an
  item are kept in the database with status "superseded"
"""

STAKEHOLDER_ROLES = (
    "Customers and end users",
    "Business analysts",
    "Product owners",
    "Software architects and developers",
    "Information-security teams",
    "Compliance and legal officers",
    "Risk-management teams",
    "Operations personnel",
    "Auditors and regulators",
)

INTERVIEW_TOPICS = (
    "Business objectives",
    "Users and roles",
    "Existing workflow",
    "Inputs, outputs and business rules",
    "Exceptional conditions",
    "Data collection and retention",
    "Authentication and authorisation",
    "Financial transaction limits",
    "Audit and reporting requirements",
    "Performance and availability",
    "Integration with existing systems",
    "Regulatory and security constraints",
    "Expected project schedule and budget",
)

REQUIREMENT_CATEGORIES = (
    "Business",
    "Stakeholder",
    "Functional",
    "Security",
    "Privacy",
    "Regulatory",
    "Performance",
    "Availability and reliability",
    "Usability",
    "Data-management",
    "Integration",
    "Audit and reporting",
    "Operational and maintenance",
)

LEVELS = ("High", "Medium", "Low")

# Phase 5A: the action is "accept", the resulting status is "approved".
APPROVAL_STATUSES = ("proposed", "approved", "rejected", "modified")


def canonical(value, allowed: tuple[str, ...]):
    """Case/whitespace-insensitive match to an allowed label; returns the input unchanged if none."""
    if not isinstance(value, str):
        return value
    key = " ".join(value.lower().split())
    for label in allowed:
        if " ".join(label.lower().split()) == key:
            return label
    return value
