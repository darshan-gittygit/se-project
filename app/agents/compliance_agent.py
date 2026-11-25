"""
Agent 3: Compliance & Risk Analyzer (RAG-grounded)

Pipeline:
1. Build a retrieval query from the domain + requirement summaries.
2. Retrieve the top-k most relevant chunks from the curated compliance
   knowledge base (app/rag/retriever.py) - real vector search, not a
   keyword lookup.
3. LLM reads the retrieved chunks + the requirements and produces
   structured compliance findings, explicitly grounded in and citing
   which knowledge base source(s) informed each finding.

If retrieval returns only weakly relevant chunks (high distance - the
scenario's domain isn't well covered by the knowledge base), the agent
is instructed to say so honestly rather than forcing an irrelevant
citation onto an unrelated domain.
"""
from app.gemini_client import generate_text
from app.json_utils import parse_json_response
from app.rag.retriever import retrieve

# Above this cosine distance, a retrieved chunk is considered too weakly
# related to be worth citing - keeps the agent from forcing a financial
# regulation onto, say, a warehouse inventory scenario.
RELEVANCE_THRESHOLD = 0.65

PROMPT_TEMPLATE = """You are a compliance and risk analyst reviewing software requirements against a curated regulatory knowledge base.

Domain: {domain}

Functional Requirements:
{functional_list}

Non-Functional Requirements:
{non_functional_list}

Retrieved knowledge base context (may or may not be relevant - judge for yourself):
{retrieved_context}

Instructions:
- Only make compliance claims that are actually supported by the retrieved context above. Do not invent regulatory requirements not present in the context.
- If the retrieved context is not meaningfully relevant to this domain, say so explicitly rather than forcing a connection.
- For each compliance consideration you identify, reference which requirement ID(s) it relates to and which knowledge base source it's grounded in (the source names are shown in the context).
- Rate the overall compliance/regulatory risk level for this project as "Low", "Medium", or "High", based on how much sensitive data, financial risk, or regulated activity the requirements imply.

Respond with ONLY valid JSON, no markdown fences, no commentary, in exactly this schema:
{{
  "risk_level": "Low" | "Medium" | "High",
  "kb_relevance": "relevant" | "partially_relevant" | "not_relevant",
  "considerations": [
    {{"related_requirement_ids": ["FR-1"], "note": "...", "source": "kb_source_name"}}
  ],
  "summary": "2-3 sentence overall compliance summary"
}}

If kb_relevance is "not_relevant", considerations should be an empty list and the summary should explain that the knowledge base doesn't cover this domain well.
"""


def _format_requirements(reqs: list[dict]) -> str:
    return "\n".join(f"- {r['id']}: {r['requirement']}" for r in reqs) or "(none)"


def _format_retrieved_context(chunks: list[dict]) -> str:
    if not chunks:
        return "(no relevant knowledge base content found)"
    parts = []
    for c in chunks:
        relevance_note = "" if c["distance"] < RELEVANCE_THRESHOLD else " [weak match - low relevance]"
        parts.append(f"[Source: {c['source']}]{relevance_note}\n{c['text']}")
    return "\n\n".join(parts)


def analyze_compliance(domain: str, functional_requirements: list[dict], non_functional_requirements: list[dict]) -> dict:
    all_reqs_text = " ".join(
        r["requirement"] for r in (functional_requirements + non_functional_requirements)
    )
    query = f"{domain}. {all_reqs_text}"

    retrieved_chunks = retrieve(query, top_k=5)
    relevant_chunks = [c for c in retrieved_chunks if c["distance"] < RELEVANCE_THRESHOLD]

    prompt = PROMPT_TEMPLATE.format(
        domain=domain,
        functional_list=_format_requirements(functional_requirements),
        non_functional_list=_format_requirements(non_functional_requirements),
        retrieved_context=_format_retrieved_context(retrieved_chunks),
    )
    raw = generate_text(prompt, temperature=0.2)
    result = parse_json_response(raw)

    result["sources_consulted"] = sorted(set(c["source"] for c in retrieved_chunks))
    result["sources_used"] = sorted(set(c["source"] for c in relevant_chunks))
    return result
