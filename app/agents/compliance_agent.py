"""
Agent 3: Compliance & Risk Analyzer (RAG-grounded)

Pipeline:
1. One short retrieval query per requirement (the requirement text),
   embedded in a single batch. Short queries keep each embedding focused;
   the old single query (domain + every requirement joined together) was so
   diluted that no chunk ever passed the relevance threshold.
2. Chunks are merged across queries (keeping each chunk's closest distance
   and which requirements it matched). Only chunks closer than
   RELEVANCE_THRESHOLD are passed to the LLM - weak matches never reach the
   prompt.
3. kb_relevance is computed from the distances, not chosen by the LLM, and
   only DOMAIN-SPECIFIC sources count: no requirement matched a
   domain-specific chunk -> "not_relevant"; at least RELEVANT_COVERAGE of the
   requirements did -> "relevant"; otherwise "partially_relevant". Sources in
   GENERIC_SOURCES (the domain-agnostic security baseline) apply to almost
   any system, so a match on them says nothing about whether the knowledge
   base covers the domain. The same rule applies to every domain.
4. The LLM writes considerations, a risk level and a summary from the
   relevant chunks only. Any consideration whose source is not among the
   relevant chunks is dropped (and reported in dropped_considerations).
   Generic-baseline citations are kept - also when kb_relevance is
   "not_relevant" - and every consideration and retrieved chunk carries a
   "scope" label: "generic baseline" or "domain-specific".
"""
from app.agents.compliance_mapping import build_mapping
from app.gemini_client import generate_text
from app.rag.kb_metadata import applies, classify_domains, generic_sources
from app.rag.retriever import known_sources, retrieve_many
from app.schemas import ComplianceOutput, generate_validated

# Squared-L2 distance (ChromaDB default, see retriever.py) below which a
# chunk counts as relevant to a requirement. Tuned with
# scripts/tune_relevance_threshold.py against scripts/retrieval_labels.json;
# results in docs/RAG_TUNING.md. Re-tune if the metric, embedding model or
# query format changes.
RELEVANCE_THRESHOLD = 0.62

TOP_K_PER_QUERY = 3          # chunks retrieved per requirement query
MAX_CONTEXT_CHUNKS = 8       # cap on relevant chunks passed to the LLM
RELEVANT_COVERAGE = 0.5      # share of requirements with a domain-specific match for "relevant"

# Knowledge-base documents that are domain-agnostic by design, marked
# "generic_baseline" in knowledge_base/sources.json. They can be cited,
# labelled SCOPE_GENERIC, but never count toward kb_relevance.
GENERIC_SOURCES = generic_sources()
SCOPE_GENERIC = "generic baseline"
SCOPE_DOMAIN = "domain-specific"


def source_scope(source: str) -> str:
    return SCOPE_GENERIC if source in GENERIC_SOURCES else SCOPE_DOMAIN

PROMPT_TEMPLATE = """You are a compliance and risk analyst reviewing software requirements against a curated regulatory knowledge base.

Domain: {domain}

Functional Requirements:
{functional_list}

Non-Functional Requirements:
{non_functional_list}

Knowledge base relevance (computed from retrieval distances using domain-specific sources only, not your choice): {kb_relevance}

Retrieved knowledge base context - only chunks that passed the relevance threshold are shown, each with its scope and the requirement IDs whose query matched it. A "generic baseline" chunk is a domain-agnostic security baseline that applies to almost any system; it does not show that the knowledge base covers this domain:
{retrieved_context}

Instructions:
- Only make compliance claims that are actually supported by the retrieved context above. Do not invent regulatory requirements not present in the context.
- Every consideration must cite one source name exactly as written after "Source:" above. Allowed sources: {allowed_sources}. Never cite anything else (no law names, articles or standards that are not one of these source names).
- Reference only requirement IDs that appear in the lists above.
- If knowledge base relevance is "not_relevant", cite only generic baseline sources (if any), and say in the summary that the knowledge base has no domain-specific coverage for this domain.
- Never present a generic baseline principle as a domain-specific regulation.
- Assess every requirement-source match shown above. For each consideration give:
  - "note": the security, privacy or compliance control the source expects for these requirements;
  - "status": "satisfied" if the requirements as written already meet it, "gap" if they do not (then describe what is missing in "compliance_gap"), or "not_applicable" if the match is a false hit;
  - "evidence": a word-for-word quote (at least 5 words) from the retrieved source text that supports the control;
  - "checkpoints": mandatory approval or audit checkpoints the source implies ([] if none);
  - "retention_and_reporting": data-retention and reporting obligations the source implies ([] if none).
- Do not make final legal determinations.
- Rate the overall compliance/regulatory risk level for this project as "Low", "Medium", or "High", based on how much sensitive data, financial risk, or regulated activity the requirements imply.

Respond with ONLY valid JSON, no markdown fences, no commentary, in exactly this schema:
{{
  "risk_level": "Low" | "Medium" | "High",
  "considerations": [
    {{"related_requirement_ids": ["FR-1"], "note": "...", "source": "kb_source_name",
      "status": "satisfied" | "gap" | "not_applicable", "evidence": "exact quote from the source text",
      "compliance_gap": null or "what is missing", "checkpoints": [], "retention_and_reporting": []}}
  ],
  "summary": "2-3 sentence overall compliance summary"
}}
"""


def _format_requirements(reqs: list[dict]) -> str:
    return "\n".join(f"- {r['id']}: {r['requirement']}" for r in reqs) or "(none)"


def build_queries(domain: str, requirements: list[dict]) -> list[str]:
    """
    One short retrieval query per requirement: the requirement text alone.
    The tuning script compared this with "domain: requirement" and the plain
    text scored slightly better (F0.5 0.909 vs 0.901). `domain` is kept in
    the signature so the format can be switched back without API changes.
    """
    return [r["requirement"] for r in requirements]


def merge_retrieval(requirements: list[dict], per_query: list[list[dict]]) -> list[dict]:
    """
    Merges per-requirement results into one record per chunk:
    {"id", "source", "text", "distance" (closest), "matched_requirement_ids"
    (requirements whose query found it under the threshold)}, closest first.
    """
    merged = {}
    for req, chunks in zip(requirements, per_query):
        for c in chunks:
            rec = merged.setdefault(c["id"], {
                "id": c["id"], "source": c["source"], "text": c["text"],
                "distance": c["distance"], "matched_requirement_ids": [],
            })
            rec["distance"] = min(rec["distance"], c["distance"])
            if c["distance"] < RELEVANCE_THRESHOLD and req["id"] not in rec["matched_requirement_ids"]:
                rec["matched_requirement_ids"].append(req["id"])
    return sorted(merged.values(), key=lambda r: (r["distance"], r["id"]))


def kb_relevance_from(requirements: list[dict], relevant_chunks: list[dict]) -> str:
    """
    Deterministic relevance label from how many requirements matched a
    relevant DOMAIN-SPECIFIC chunk whose source applies to the scenario's
    domain (knowledge_base/sources.json "applies_to"). Generic-baseline chunks
    and chunks from sources for other domains are ignored here.
    """
    matched = {rid for c in relevant_chunks
               if c["source"] not in GENERIC_SOURCES and c.get("applies_to_domain", True)
               for rid in c["matched_requirement_ids"]}
    if not matched:
        return "not_relevant"
    if requirements and len(matched) / len(requirements) >= RELEVANT_COVERAGE:
        return "relevant"
    return "partially_relevant"


def _format_retrieved_context(chunks: list[dict]) -> str:
    if not chunks:
        return "(no knowledge base content passed the relevance threshold)"
    return "\n\n".join(
        f"[Source: {c['source']} | scope: {source_scope(c['source'])} | chunk {c['id']} | "
        f"matched: {', '.join(c['matched_requirement_ids'])}]\n{c['text']}"
        for c in chunks
    )


def filter_considerations(considerations: list[dict], allowed_sources: set[str]) -> tuple[list[dict], list[dict]]:
    """Keeps considerations citing a relevant retrieved source; returns (kept, dropped)."""
    kept, dropped = [], []
    for c in considerations:
        if c.get("source") in allowed_sources:
            kept.append(c)
        else:
            dropped.append({**c, "reason": "source was not among the relevant retrieved chunks"})
    return kept, dropped


def analyze_compliance(domain: str, functional_requirements: list[dict], non_functional_requirements: list[dict],
                       scenario: str | None = None) -> dict:
    requirements = functional_requirements + non_functional_requirements
    per_query = retrieve_many(build_queries(domain, requirements), top_k=TOP_K_PER_QUERY)
    merged = merge_retrieval(requirements, per_query)
    # Which KB sources apply: deterministic domain tags from the domain label,
    # the scenario (when given) and the requirement statements.
    domain_tags = classify_domains([domain, scenario or ""] + [r["requirement"] for r in requirements])
    for c in merged:
        c["applies_to_domain"] = applies(c["source"], domain_tags)
    under_threshold = [c for c in merged if c["distance"] < RELEVANCE_THRESHOLD]
    # Chunks from sources for other domains (e.g. KYC/AML for payroll) never
    # reach the prompt, cannot be cited and do not count toward relevance.
    applicable = [c for c in under_threshold if c["applies_to_domain"]]
    # Relevance uses every applicable chunk under the threshold; the cap only limits
    # the prompt (otherwise closer generic chunks could crowd out a domain match).
    kb_relevance = kb_relevance_from(requirements, applicable)
    relevant = applicable[:MAX_CONTEXT_CHUNKS]
    sources_used = sorted({c["source"] for c in relevant})

    prompt = PROMPT_TEMPLATE.format(
        domain=domain,
        functional_list=_format_requirements(functional_requirements),
        non_functional_list=_format_requirements(non_functional_requirements),
        kb_relevance=kb_relevance,
        retrieved_context=_format_retrieved_context(relevant),
        allowed_sources=", ".join(sources_used) or "(none)",
    )
    # Schema check: risk_level enum, known requirement ids, and sources that
    # exist in the knowledge base at all (an invented name like "GDPR Article
    # 17" is invalid output and triggers the retry). A real source that was
    # not retrieved for THIS request is not an error - it is dropped below.
    result = generate_validated(
        prompt, ComplianceOutput, generate=generate_text, temperature=0.2,
        context={"requirement_ids": {r["id"] for r in requirements}, "kb_sources": set(known_sources())},
    ).model_dump()

    kept, dropped = filter_considerations(result["considerations"], set(sources_used))
    if kb_relevance == "not_relevant":
        # Only generic-baseline chunks can be relevant here, so the filter above
        # has already removed domain-specific citations; this keeps the rule explicit.
        dropped += [{**c, "reason": "no domain-specific knowledge base coverage"}
                    for c in kept if c["source"] not in GENERIC_SOURCES]
        kept = [c for c in kept if c["source"] in GENERIC_SOURCES]
    # Per-requirement mapping (section 11) with code-set metadata, confidence and
    # review flags. Findings judged not applicable stay in the mapping (and send
    # the requirement to review) but are not citations.
    compliance_mapping = build_mapping(requirements, kept, relevant, source_scope, GENERIC_SOURCES)
    kept = [{**c, "scope": source_scope(c["source"])} for c in kept if c.get("status") != "not_applicable"]

    result["kb_relevance"] = kb_relevance
    result["considerations"] = kept
    result["sources_consulted"] = sorted({c["source"] for c in merged})
    result["sources_used"] = sources_used
    # Additive fields: the evidence behind kb_relevance and any filtered output.
    result["domain_tags"] = domain_tags
    result["retrieved_chunks"] = [
        {"id": c["id"], "source": c["source"], "scope": source_scope(c["source"]),
         "distance": round(c["distance"], 4), "matched_requirement_ids": c["matched_requirement_ids"],
         "applies_to_domain": c["applies_to_domain"]}
        for c in under_threshold
    ]
    result["dropped_considerations"] = dropped
    result["compliance_mapping"] = compliance_mapping
    return result


def attach_regulations(requirements: dict, compliance_result: dict) -> dict:
    """
    Fills each requirement record's applicable_regulations (problem statement
    section 8) with the DOMAIN-SPECIFIC knowledge-base sources that the kept
    compliance considerations cite for its id. The generic baseline is not a
    regulation, so it is left out. Agent 1 never sets this field itself.
    Mutates and returns `requirements`.
    """
    by_id: dict[str, set[str]] = {}
    for c in compliance_result.get("considerations", []):
        if c.get("source") in GENERIC_SOURCES:
            continue
        for rid in c.get("related_requirement_ids", []):
            by_id.setdefault(rid, set()).add(c["source"])
    for key in ("functional_requirements", "non_functional_requirements"):
        for r in requirements.get(key, []):
            r["applicable_regulations"] = sorted(by_id.get(r["id"], set()))
    return requirements
