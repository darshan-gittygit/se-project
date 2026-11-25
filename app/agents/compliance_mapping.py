"""
Per-requirement compliance mapping (problem statement section 11) with
confidence grading and escalation for human review (section 6, steps 5-6).

The LLM supplies the judgement in each consideration (status, required
control, evidence quote, gap, checkpoints, retention/reporting obligations).
Everything else is set here, by code:
- regulation title, jurisdiction, version, effective date: knowledge_base/sources.json
- chunk id and distance: the retrieval match for that requirement and source
- evidence_verified: the quote really appears in the retrieved source text
- confidence: graded from distance and evidence (bands below)
- needs_human_review + review_reasons: escalation rules below

This is not a legal determination; NOTICE says so in every response.
"""
from app.rag.kb_metadata import load_metadata, source_info
from app.schemas import _normalise

# Confidence bands from the labelled tuning set (docs/RAG_TUNING.md):
# relevant pairs have a median distance of 0.52 and no irrelevant pair was
# closer than 0.551. Below 0.50 is well inside the relevant range (High);
# 0.50-0.55 is still closer than every irrelevant pair seen (Medium); from
# 0.55 to the threshold relevant and irrelevant pairs overlap (Low).
HIGH_CONFIDENCE_DISTANCE = 0.50
MEDIUM_CONFIDENCE_DISTANCE = 0.55
MIN_EVIDENCE_WORDS = 5

NOTICE = ("Not a legal determination. Mappings are to a small curated knowledge base of paraphrased "
          "principles, not verbatim law. High-impact interpretations must be approved by compliance "
          "or legal officers.")


def candidate_pairs(chunks: list[dict]) -> dict:
    """{(requirement_id, source): {"chunk_id", "distance"}} - the closest retrieved match per pair."""
    best = {}
    for c in chunks:
        for rid in c["matched_requirement_ids"]:
            key = (rid, c["source"])
            if key not in best or c["distance"] < best[key]["distance"]:
                best[key] = {"chunk_id": c["id"], "distance": c["distance"]}
    return best


def evidence_verified(evidence: str, source: str, chunks: list[dict]) -> bool:
    """True if the quote has enough words and appears (case/punctuation-insensitive) in a retrieved chunk of that source."""
    quote = _normalise(evidence or "")
    if len(quote.split()) < MIN_EVIDENCE_WORDS:
        return False
    return any(quote in _normalise(c["text"]) for c in chunks if c["source"] == source)


def grade_confidence(distance: float | None, verified: bool) -> str:
    if distance is None or not verified or distance >= MEDIUM_CONFIDENCE_DISTANCE:
        return "Low"
    return "High" if distance < HIGH_CONFIDENCE_DISTANCE else "Medium"


def _item(rid, source, cand, consideration, chunks, scope):
    meta = load_metadata().get(source, {})
    verified = bool(consideration) and evidence_verified(consideration.get("evidence", ""), source, chunks)
    distance = round(cand["distance"], 4) if cand else None
    return {
        "source": source,
        "regulation": meta.get("title", source),
        "scope": scope,
        **source_info(source),
        "chunk_id": cand["chunk_id"] if cand else None,
        "distance": distance,
        "status": (consideration or {}).get("status") or "not_assessed",
        "required_control": (consideration or {}).get("note", ""),
        "evidence": (consideration or {}).get("evidence", ""),
        "evidence_verified": verified,
        "compliance_gap": (consideration or {}).get("compliance_gap"),
        "checkpoints": (consideration or {}).get("checkpoints", []),
        "retention_and_reporting": (consideration or {}).get("retention_and_reporting", []),
        "confidence": grade_confidence(distance, verified),
    }


def _review_reasons(req: dict, items: list[dict], generic: frozenset) -> list[str]:
    reasons = []
    for it in items:
        src = it["source"]
        if it["status"] == "not_assessed":
            reasons.append(f"{src}: retrieval matched this requirement but the analysis did not assess it")
        elif it["status"] == "not_applicable":
            reasons.append(f"{src}: retrieval matched this requirement but the analysis judged it not applicable (ambiguous match)")
        elif it["distance"] is None:
            reasons.append(f"{src}: cited without a retrieval match for this requirement")
        elif it["confidence"] == "Low":
            why = f"distance {it['distance']:.2f}" + ("" if it["evidence_verified"] else ", evidence quote not found in the source text")
            reasons.append(f"{src}: low-confidence match ({why})")
        if it["status"] == "gap" and req.get("risk_level") == "High":
            reasons.append(f"{src}: compliance gap on a high-risk requirement; the interpretation needs compliance or legal approval")
    supported = [it for it in items if it["source"] not in generic and it["status"] in ("satisfied", "gap")]
    if "Regulatory" in (req.get("categories") or []) and not supported:
        reasons.append("regulatory requirement with no domain-specific knowledge-base support")
    return reasons


def build_mapping(requirements: list[dict], considerations: list[dict], chunks: list[dict],
                  scope_of, generic: frozenset) -> dict:
    """
    requirements: requirement dicts (risk_level / categories used when present)
    considerations: validated considerations whose sources passed retrieval (any status)
    chunks: the retrieved chunks that were in the prompt
    """
    cands = candidate_pairs(chunks)
    by_req = {r["id"]: [] for r in requirements}
    covered = set()
    for c in considerations:
        for rid in c["related_requirement_ids"]:
            if rid in by_req and (rid, c["source"]) not in covered:
                covered.add((rid, c["source"]))
                by_req[rid].append(_item(rid, c["source"], cands.get((rid, c["source"])), c, chunks, scope_of(c["source"])))
    for (rid, source), cand in cands.items():
        if rid in by_req and (rid, source) not in covered:
            by_req[rid].append(_item(rid, source, cand, None, chunks, scope_of(source)))
    entries = []
    for r in requirements:
        items = sorted(by_req[r["id"]], key=lambda it: (it["distance"] is None, it["distance"] or 0, it["source"]))
        reasons = _review_reasons(r, items, generic)
        entries.append({"requirement_id": r["id"], "mappings": items,
                        "needs_human_review": bool(reasons), "review_reasons": reasons})
    return {"notice": NOTICE, "requirements": entries,
            "review_count": sum(e["needs_human_review"] for e in entries)}
