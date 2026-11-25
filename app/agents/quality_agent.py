"""
Requirement quality and conflict checks (problem statement section 10).

Deterministic rules first (no LLM):
- ambiguity:  vague words from VAGUE_TERMS in the requirement statement
- untestable: a Performance or Availability-and-reliability NFR with no
              number anywhere in its statement or acceptance criteria
- duplicate:  two statements whose wording is near-identical
              (difflib ratio >= DUPLICATE_SIMILARITY after dropping "the system shall")
Then ONE LLM call, only for conflicts (requirements that cannot both be met
as written), validated by ConflictOutput (retry once, then LLMOutputError).

Every flag: {"requirement_ids", "type", "reason", "suggested_fix",
"detected_by": "rule" | "llm"}.
"""
import difflib
import re
from itertools import combinations

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from app.gemini_client import generate_text
from app.schemas import _NonEmptyText, _ctx, generate_validated

FLAG_TYPES = ("ambiguity", "untestable", "duplicate", "conflict")

# Words and phrases that cannot be verified without a definition. A curated
# list for this project; extend it here. Matched case-insensitively on word
# boundaries (so "fast" does not match "breakfast").
VAGUE_TERMS = (
    "as soon as possible", "user-friendly", "user friendly", "easy to use", "state-of-the-art",
    "high performance", "highly available", "in a timely manner", "if possible", "as appropriate",
    "as applicable", "but not limited to", "and/or", "etc",
    "fast", "quick", "quickly", "rapid", "rapidly", "slow", "easy", "easily", "simple", "intuitive",
    "efficient", "efficiently", "flexible", "robust", "seamless", "seamlessly", "adequate",
    "adequately", "appropriate", "appropriately", "sufficient", "reasonable", "timely", "promptly",
    "optimal", "best", "better", "improved", "maximise", "maximize", "minimise", "minimize",
    "minimal", "modern", "normal", "large", "several", "various", "secure", "securely",
)
_VAGUE_RE = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(t) for t in sorted(VAGUE_TERMS, key=len, reverse=True)) + r")(?![\w-])",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d")
# NFR categories that are only testable against a number (live loan run:
# "immutable audit trail" was wrongly flagged when every NFR was checked).
MEASURABLE_CATEGORIES = frozenset({"Performance", "Availability and reliability"})
DUPLICATE_SIMILARITY = 0.92  # 0.90 flagged "encrypt payment data" vs "encrypt user data" (0.901)


def _flag(ids, kind, reason, fix, by="rule"):
    return {"requirement_ids": list(ids), "type": kind, "reason": reason, "suggested_fix": fix, "detected_by": by}


def _core_text(text: str) -> str:
    t = " ".join(re.sub(r"[^a-z0-9%]+", " ", text.lower()).split())
    return re.sub(r"^the system (shall|must|should|will) ", "", t)


def ambiguity_flags(reqs: list[dict]) -> list[dict]:
    flags = []
    for r in reqs:
        terms = list(dict.fromkeys(m.group(1).lower() for m in _VAGUE_RE.finditer(r["requirement"])))
        if not terms:
            continue
        measurable = [c for c in r.get("acceptance_criteria") or [] if _NUMBER_RE.search(c)]
        fix = (f"Replace {', '.join(repr(t) for t in terms)} with the measurable value from its acceptance "
               f"criteria ({measurable[0]!r})." if measurable else
               f"Replace {', '.join(repr(t) for t in terms)} with a measurable value or a named standard/control.")
        flags.append(_flag([r["id"]], "ambiguity", f"Vague term(s): {', '.join(terms)}.", fix))
    return flags


def untestable_flags(nfrs: list[dict]) -> list[dict]:
    """
    Only NFRs categorised Performance or Availability and reliability: those
    are testable only against a number. Other NFRs (e.g. an immutable audit
    trail, encryption) can be verified without one, so "no number" would be a
    false positive there. NFRs without categories are not checked.
    """
    flags = []
    for r in nfrs:
        if not MEASURABLE_CATEGORIES.intersection(r.get("categories") or []):
            continue
        texts = [r["requirement"]] + list(r.get("acceptance_criteria") or [])
        if not any(_NUMBER_RE.search(t) for t in texts):
            flags.append(_flag([r["id"]], "untestable",
                               "No measurable criterion: neither the statement nor its acceptance criteria give a number, unit or threshold.",
                               "Add a measurable target (for example a time, percentage, count or limit) and how it will be measured."))
    return flags


def duplicate_flags(reqs: list[dict]) -> list[dict]:
    flags = []
    for a, b in combinations(reqs, 2):
        ratio = difflib.SequenceMatcher(None, _core_text(a["requirement"]), _core_text(b["requirement"])).ratio()
        if ratio >= DUPLICATE_SIMILARITY:
            flags.append(_flag([a["id"], b["id"]], "duplicate",
                               f"Near-identical wording (similarity {ratio:.2f}).",
                               f"Merge {a['id']} and {b['id']} if they mean the same thing; otherwise reword to make the difference explicit."))
    return flags


# --- conflicts: one LLM call -------------------------------------------------

class Conflict(_NonEmptyText):
    requirement_ids: list[str] = Field(min_length=2)
    reason: str = Field(min_length=1)
    suggested_fix: str = Field(min_length=1)

    @field_validator("requirement_ids")
    @classmethod
    def _distinct_known_ids(cls, ids, info: ValidationInfo):
        if len(set(ids)) != len(ids) or len(ids) < 2:
            raise ValueError("a conflict needs at least two different requirement ids")
        known = _ctx(info, "requirement_ids")
        unknown = [i for i in ids if known is not None and i not in known]
        if unknown:
            raise ValueError(f"unknown requirement ids {unknown}")
        return ids


class ConflictOutput(BaseModel):
    conflicts: list[Conflict] = Field(default_factory=list)


CONFLICT_PROMPT = """You are reviewing a software requirements list for CONFLICTS.

Domain: {domain}

Requirements (id: statement | acceptance criteria):
{requirements}

A conflict means two or more requirements cannot all be fully satisfied as written: contradictory values or limits, mutually exclusive behaviour, or constraints that work against each other (for example a 1-second response time and a 10-second fraud check on the same request). Duplicates, overlaps and requirements that are merely related are NOT conflicts. Report only genuine conflicts; it is fine and common to report none.

Respond with ONLY valid JSON, no markdown fences, in exactly this schema:
{{"conflicts": [{{"requirement_ids": ["FR-1", "NFR-2"], "reason": "why they cannot both be met", "suggested_fix": "how to resolve it"}}]}}
"""


def conflict_flags(domain: str, reqs: list[dict]) -> list[dict]:
    if len(reqs) < 2:
        return []
    listing = "\n".join(
        f"- {r['id']}: {r['requirement']}" + (f" | {'; '.join(r['acceptance_criteria'])}" if r.get("acceptance_criteria") else "")
        for r in reqs
    )
    out = generate_validated(
        CONFLICT_PROMPT.format(domain=domain, requirements=listing), ConflictOutput,
        generate=generate_text, temperature=0.1,
        context={"requirement_ids": {r["id"] for r in reqs}},
    )
    return [_flag(c.requirement_ids, "conflict", c.reason, c.suggested_fix, by="llm") for c in out.conflicts]


def analyze_quality(domain: str, functional_requirements: list[dict], non_functional_requirements: list[dict]) -> dict:
    reqs = functional_requirements + non_functional_requirements
    flags = (ambiguity_flags(reqs) + untestable_flags(non_functional_requirements)
             + duplicate_flags(reqs) + conflict_flags(domain, reqs))
    return {"flags": flags, "counts": {t: sum(f["type"] == t for f in flags) for t in FLAG_TYPES}}
