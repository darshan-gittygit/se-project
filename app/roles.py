"""
Roles and permissions (problem statement section 16 approvals; section 17
"role-based access control" and "agent-level permissions").

A SIMPLE STAND-IN: the role is whatever the caller puts in the X-Role header
(the UI will offer a dropdown later). Nothing proves who the caller is. Real
deployments need authentication, MFA and server-side sessions; see
docs/ARCHITECTURE.md. The permission table is still enforced on every call.

Requests without the header keep working where they did before (the current
UI sends none) and are logged as "unspecified"; approval actions and the
audit log always require a role.
"""

BUSINESS_ANALYST = "business analyst"
PRODUCT_OWNER = "product owner"
SECURITY_OFFICER = "security officer"
COMPLIANCE_OFFICER = "compliance officer"
PROJECT_MANAGER = "project manager"
ARCHITECT = "architect"
AUDITOR = "auditor"

ROLES = (BUSINESS_ANALYST, PRODUCT_OWNER, SECURITY_OFFICER, COMPLIANCE_OFFICER, PROJECT_MANAGER, ARCHITECT, AUDITOR)
UNSPECIFIED = "unspecified"

# Who may do what. Approval actions (accept, reject, modify, regenerate) on an
# item need the item's permission; section 16 mapping in the comments.
PERMISSIONS = {
    "run_pipeline": {BUSINESS_ANALYST, PRODUCT_OWNER, SECURITY_OFFICER, COMPLIANCE_OFFICER, PROJECT_MANAGER, ARCHITECT},
    # Requirements in general; "Final requirement baselines" sit with the same roles.
    "review_requirement": {BUSINESS_ANALYST, PRODUCT_OWNER},
    # "High-risk security requirements": a Security-category requirement with risk level High.
    "review_security_requirement": {SECURITY_OFFICER},
    # "Regulatory interpretations".
    "review_compliance": {COMPLIANCE_OFFICER},
    # "SDLC selection" - section 14 names all four, so all four must accept.
    "review_sdlc": {PROJECT_MANAGER, ARCHITECT, SECURITY_OFFICER, COMPLIANCE_OFFICER},
    "freeze_baseline": {PRODUCT_OWNER, BUSINESS_ANALYST},
    "read_audit": {AUDITOR},
}
SDLC_APPROVERS_REQUIRED = PERMISSIONS["review_sdlc"]


class RoleError(Exception):
    """Unknown role value (400)."""


class PermissionDenied(Exception):
    """Role not allowed to do this (403)."""


def parse_role(header: str | None) -> str:
    """Normalises the X-Role header; UNSPECIFIED if absent; RoleError if not a known role."""
    if header is None or not header.strip():
        return UNSPECIFIED
    role = " ".join(header.strip().lower().replace("_", " ").replace("-", " ").split())
    if role not in ROLES:
        raise RoleError(f"Unknown role {header!r}. Use one of: {', '.join(ROLES)}.")
    return role


def require(permission: str, role: str, allow_unspecified: bool = False) -> None:
    if role == UNSPECIFIED and allow_unspecified:
        return
    if role == UNSPECIFIED:
        raise PermissionDenied(f"This action needs an X-Role header with one of: {', '.join(sorted(PERMISSIONS[permission]))}.")
    if role not in PERMISSIONS[permission]:
        raise PermissionDenied(f"Role {role!r} may not {permission.replace('_', ' ')}; allowed: "
                               f"{', '.join(sorted(PERMISSIONS[permission]))}.")


def review_permission(item_type: str, content: dict) -> str:
    """Which permission reviewing this item needs."""
    if item_type == "requirement":
        high_risk_security = content.get("risk_level") == "High" and "Security" in (content.get("categories") or [])
        return "review_security_requirement" if high_risk_security else "review_requirement"
    return {"compliance": "review_compliance", "sdlc": "review_sdlc"}[item_type]
