"""
Requirement artefacts built by code from data the pipeline already has
(problem statement section 12). No LLM call:

- traceability_matrix:       requirement -> source stakeholder statement ->
                             compliance sources -> risk ids -> user story /
                             use case ids (and workflow items linked to it)
- compliance_control_matrix: one row per requirement x knowledge-base source,
                             flattened from compliance_mapping
- assumptions_register:      project and per-requirement assumptions, plus
                             the dependencies between requirements
- open_issues:               quality flags, compliance review flags, pending
                             approvals and items nobody has verified yet

Section 12's last line - "each generated item should remain linked to its
original stakeholder statement and supporting document" - is served by
source_statements(): every row carries the statement it came from.
"""

GENERATED_KINDS = ("user_stories", "use_cases", "process_workflows", "data_requirements", "interface_requirements")


def all_requirements(requirements: dict) -> list[dict]:
    return requirements.get("functional_requirements", []) + requirements.get("non_functional_requirements", [])


def source_statement(req: dict) -> dict:
    """The original stakeholder statement behind one requirement (empty text when it rests on assumptions)."""
    out = {"requirement_id": req["id"], "stakeholder": req.get("source_stakeholder", ""),
           "statement": req.get("source_text", ""), "assumptions": list(req.get("assumptions", []))}
    if req.get("source_document"):
        out["document"] = req["source_document"]   # additive: the uploaded file the quote is from
    return out


def source_statements(requirement_ids: list[str], requirements: dict) -> list[dict]:
    by_id = {r["id"]: r for r in all_requirements(requirements)}
    return [source_statement(by_id[i]) for i in requirement_ids if i in by_id]


def _ids_linking(items: list[dict], rid: str) -> list[str]:
    return [it["id"] for it in items if rid in it.get("requirement_ids", it.get("linked_requirement_ids", []))]


def traceability_matrix(requirements: dict, compliance_mapping: dict | None, risk_register: dict | None,
                        generated: dict | None, workflow: dict | None) -> list[dict]:
    mapping = {e["requirement_id"]: e for e in (compliance_mapping or {}).get("requirements", [])}
    risks = (risk_register or {}).get("risks", [])
    gen = generated or {}
    wf_items = [(p["name"], i["text"], i["requirement_ids"]) for p in (workflow or {}).get("phases", [])
                for f, items in p.items() if isinstance(items, list) for i in items if isinstance(i, dict)]
    rows = []
    for r in all_requirements(requirements):
        rid = r["id"]
        entry = mapping.get(rid, {"mappings": []})
        rows.append({
            "requirement_id": rid,
            "type": r.get("type"),
            "requirement": r["requirement"],
            "source": source_statement(r),
            "compliance_sources": [{"source": m["source"], "status": m["status"], "confidence": m["confidence"]}
                                   for m in entry["mappings"] if m["status"] in ("satisfied", "gap")],
            "risk_ids": _ids_linking(risks, rid),
            **{f"{kind}_ids": _ids_linking(gen.get(kind, []), rid) for kind in GENERATED_KINDS},
            "workflow_items": [f"{phase}: {text}" for phase, text, ids in wf_items if rid in ids],
            "approval_status": r.get("approval_status", "proposed"),
        })
    return rows


def compliance_control_matrix(compliance_mapping: dict | None) -> list[dict]:
    rows = []
    for e in (compliance_mapping or {}).get("requirements", []):
        for m in e["mappings"]:
            rows.append({
                "requirement_id": e["requirement_id"], "regulation": m["regulation"], "source": m["source"],
                "scope": m["scope"], "jurisdiction": m["jurisdiction"], "version": m["version"],
                "effective_date": m["effective_date"], "required_control": m["required_control"],
                "status": m["status"], "compliance_gap": m["compliance_gap"],
                "evidence": m["evidence"], "evidence_verified": m["evidence_verified"],
                "checkpoints": m["checkpoints"], "retention_and_reporting": m["retention_and_reporting"],
                "confidence": m["confidence"], "needs_human_review": e["needs_human_review"],
            })
    return rows


def assumptions_register(requirements: dict) -> dict:
    """Project-level and per-requirement assumptions (identical texts merged) and dependencies."""
    entries: dict[str, dict] = {}

    def add(text, rid, scope):
        key = " ".join(text.lower().split())
        e = entries.setdefault(key, {"text": text, "scope": scope, "requirement_ids": []})
        if rid and rid not in e["requirement_ids"]:
            e["requirement_ids"].append(rid)

    for text in requirements.get("assumptions", []):
        add(text, None, "project")
    for r in all_requirements(requirements):
        for text in r.get("assumptions", []):
            add(text, r["id"], "requirement")
    assumptions = [{"id": f"A-{i}", **e} for i, e in enumerate(entries.values(), start=1)]
    dependencies = [{"requirement_id": r["id"], "depends_on": list(r.get("dependencies", []))}
                    for r in all_requirements(requirements) if r.get("dependencies")]
    return {"assumptions": assumptions, "dependencies": dependencies}


def open_issues(requirements: dict, quality: dict | None, compliance_mapping: dict | None,
                sdlc: dict | None, generated: dict | None, workflow: dict | None) -> list[dict]:
    issues = []

    def add(kind, description, ids, origin):
        issues.append({"id": f"ISSUE-{len(issues) + 1}", "type": kind, "description": description,
                       "requirement_ids": list(ids), "origin": origin})

    for f in (quality or {}).get("flags", []):
        add(f["type"], f"{f['reason']} Suggested fix: {f['suggested_fix']}", f["requirement_ids"], f"quality check ({f['detected_by']})")
    for e in (compliance_mapping or {}).get("requirements", []):
        for reason in e["review_reasons"]:
            add("compliance review", reason, [e["requirement_id"]], "compliance mapping")
    reqs = all_requirements(requirements)
    pending = [r["id"] for r in reqs if r.get("approval_status", "proposed") == "proposed"]
    if pending:
        add("pending approval", f"{len(pending)} requirement(s) are proposed and not yet approved (requirement baseline).",
            pending, "requirement records")
    low = [r["id"] for r in reqs if r.get("confidence") == "Low"]
    if low:
        add("unverified", "Requirement(s) have no quote from a stakeholder statement and rest on assumptions only.",
            low, "requirement records")
    if sdlc:
        approval = sdlc.get("approval_required", {})
        add("pending approval", f"SDLC model {sdlc.get('recommended_model')} awaits approval by "
            f"{', '.join(approval.get('approvers', []))}.", [], "SDLC recommendation")
        if sdlc.get("is_close_call"):
            runner = sdlc["scored_models"][1]
            add("close call", f"{sdlc['recommended_model']} is within 5 points of {runner['model']} "
                f"({runner['percent'] if 'percent' in runner else runner['score']}); review the choice.", [], "SDLC recommendation")
    generated_count = sum(len((generated or {}).get(k, [])) for k in GENERATED_KINDS)
    if generated_count:
        add("unverified", f"{generated_count} generated artefact item(s) (user stories, use cases, workflows, data and "
            "interface requirements) are LLM output that nobody has reviewed yet.", [], "artefact generation")
    added = (workflow or {}).get("added_items", 0)
    if added:
        add("unverified", f"{added} scenario-specific workflow item(s) were added by the LLM and are not reviewed yet.",
            (workflow or {}).get("requirements_linked", []), "workflow generator")
    return issues


def build_code_artefacts(requirements: dict, compliance_mapping: dict | None, risk_register: dict | None,
                         quality: dict | None, sdlc: dict | None, workflow: dict | None,
                         generated: dict | None) -> dict:
    return {
        "traceability_matrix": traceability_matrix(requirements, compliance_mapping, risk_register, generated, workflow),
        "compliance_control_matrix": compliance_control_matrix(compliance_mapping),
        "assumptions_register": assumptions_register(requirements),
        "open_issues": open_issues(requirements, quality, compliance_mapping, sdlc, generated, workflow),
    }
