"""
Knowledge-base metadata (knowledge_base/sources.json) and the domain
classification that decides which sources apply to a scenario.

Domain tags: the eight financial services in problem statement section 1,
word for word, plus two tags the knowledge base needs:
- "Healthcare" (the KB has a healthcare-privacy document), and
- "Card or online payments": an activity, not a financial service, because
  card-payment security applies to any system that takes payments (e.g. a
  food-delivery app) even though KYC/AML does not.

classify_domains() is deterministic keyword matching over the domain label,
the scenario and the requirement statements (problem statement section 6,
step 1, "Classify the stakeholder query or requirement"). It is a heuristic:
tags are returned in the response so a reviewer can see and question them.
"""
import json
import re
from functools import lru_cache

from app.config import KNOWLEDGE_BASE_DIR

METADATA_FILE = KNOWLEDGE_BASE_DIR / "sources.json"
ANY = "any"

DOMAIN_KEYWORDS = {
    "Digital banking and mobile banking":
        r"\b(?:mobile|internet|online|digital|net|retail)[ -]?banking\b|\bbanking (?:app|application|platform|customers?|services?)\b"
        r"|\bsavings accounts?\b|\baccount balances?\b",
    "Loan origination and credit assessment":
        r"\bloans?\b|\blending\b|\blenders?\b|\bborrowers?\b|\bmortgages?\b"
        r"|\bcredit (?:scor\w*|assessment|decisions?|bureau|limits?|applications?|checks?)\b",
    "Payment processing":
        r"\bpayment (?:gateways?|service providers?|processors?|processing)\b|\bupi\b|\be-?wallets?\b"
        r"|\b(?:fund|money) transfers?\b|\bremittances?\b|\bmerchant acquiring\b",
    "Fraud detection": r"\bfraud\w*\b",
    "Insurance and investment platforms":
        r"\binsurance\b|\binsurers?\b|\bpolicyholders?\b|\bunderwriting\b|\binvestments?\b|\bportfolios?\b"
        r"|\bmutual funds?\b|\bbrokerage\b|\btrading platform\b",
    "Regulatory reporting": r"\bregulatory reports?\w*\b|\breports? to (?:the )?regulators?\b",
    "Customer onboarding and KYC":
        r"\bkyc\b|\bknow your customer\b|\b(?:customer|client|account) onboarding\b|\baccount opening\b",
    "Financial data analytics": r"\bfinancial (?:data )?analytics\b|\b(?:credit|risk) analytics\b",
    "Healthcare":
        r"\bpatients?\b|\bclinic(?:al|s)?\b|\bmedical\b|\bhealth ?care\b|\bhealth (?:records?|data)\b"
        r"|\bhospitals?\b|\btelehealth\b|\bprescriptions?\b|\bdiagnos\w*\b",
    "Card or online payments":
        r"\bcard (?:payments?|data|details|numbers?)\b|\bcardholders?\b|\bcredit cards?\b|\bdebit cards?\b"
        r"|\bcheckout\b|\b(?:online|digital|in-app|mobile) payments?\b|\bpayment methods?\b|\bpay online\b"
        r"|\bprocess(?:es|ing)? (?:\w+ ){0,2}payments?\b",
}
DOMAIN_TAGS = tuple(DOMAIN_KEYWORDS)
_DOMAIN_RES = {tag: re.compile(rx, re.IGNORECASE) for tag, rx in DOMAIN_KEYWORDS.items()}


@lru_cache(maxsize=1)
def load_metadata() -> dict:
    """{source_name: {title, source, jurisdiction, version, effective_date, applies_to, generic_baseline}}"""
    return json.loads(METADATA_FILE.read_text(encoding="utf-8"))["sources"]


def generic_sources() -> frozenset:
    return frozenset(s for s, m in load_metadata().items() if m["generic_baseline"])


def classify_domains(texts: list[str]) -> list[str]:
    """Domain tags whose keywords appear in any of the texts, in DOMAIN_TAGS order."""
    blob = "\n".join(t for t in texts if t)
    return [tag for tag in DOMAIN_TAGS if _DOMAIN_RES[tag].search(blob)]


def applies(source: str, domain_tags: list[str]) -> bool:
    """True if the source applies to every domain ("any") or to one of the scenario's tags."""
    meta = load_metadata().get(source)
    if meta is None:
        return False
    targets = meta["applies_to"]
    return ANY in targets or bool(set(targets) & set(domain_tags))


def source_info(source: str) -> dict:
    """Jurisdiction, version and effective date for a source (empty strings if unknown)."""
    meta = load_metadata().get(source, {})
    return {k: meta.get(k, "") for k in ("jurisdiction", "version", "effective_date")}
