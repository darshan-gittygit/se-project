"""
Human approval actions (problem statement section 16): accept, reject,
modify and request regeneration of AI outputs - requirements, compliance
interpretations and the SDLC selection - plus freezing the requirement
baseline.

Statuses of an item's CURRENT version: proposed (generated) -> approved |
rejected, or modified (a reviewer changed it and it awaits approval).
Earlier versions are kept with status "superseded". Rules:

- accept / reject: only on a current version that is proposed or modified.
  The SDLC selection is approved only once all four section-14 approvers
  (project manager, architect, security officer, compliance officer) have
  accepted the same version; one reject is enough to reject it.
- modify: the reviewer supplies changed content; a new version is created
  with status "modified", which needs approval again - also when the item
  was already approved ("changes to approved requirements"). Content is
  checked with the same schemas as generated output.
- regenerate: re-runs the item's stage with the reviewer's comment and
  creates a new "proposed" version; at most MAX_REGENERATIONS per item.
- freeze baseline: only when every mandatory item's current version is
  approved. Any later change to an item reopens the baseline.

Every action writes an append-only decision row and an audit event.
"""
import json

from pydantic import ValidationError

from app import roles
from app.audit import append_event, now
from app.db import connect
from app.runs import current_items, get_run, item_versions, sdlc_accepts
from app.schemas import Consideration, Requirement, complete_requirement

ACTIONS = ("accept", "reject", "modify", "regenerate")
MAX_REGENERATIONS = 3
OPEN_STATUSES = ("proposed", "modified")
REQUIREMENT_EDITABLE = ("requirement", "priority", "categories", "source_stakeholder", "source_text",
                        "business_justification", "dependencies", "assumptions", "acceptance_criteria", "risk_level")
COMPLIANCE_EDITABLE = ("status", "note", "compliance_gap", "checkpoints", "retention_and_reporting")


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class InvalidInput(Exception):
    pass


class RegenerationFailed(Exception):
    """The stage re-run failed (e.g. invalid LLM output after the retry): 502, nothing stored."""


def _current(run_id, item_type, item_id) -> dict:
    versions = item_versions(run_id, item_type, item_id)
    if not versions:
        raise NotFound(f"No {item_type} item {item_id!r} in run {run_id}.")
    return versions[-1]


def _record_decision(conn, run_id, item, action, role, comment):
    conn.execute("INSERT INTO decisions (run_id, item_type, item_id, version, action, role, comment, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (run_id, item["item_type"], item["item_id"], item["version"], action, role, comment, now()))


def _set_status(conn, run_id, item, status):
    conn.execute("UPDATE approval_items SET status = ?, updated_at = ? WHERE run_id = ? AND item_type = ? "
                 "AND item_id = ? AND version = ?", (status, now(), run_id, item["item_type"], item["item_id"], item["version"]))


def _new_version(conn, run_id, item, content, status, regenerations):
    _set_status(conn, run_id, item, "superseded")
    ts = now()
    conn.execute("INSERT INTO approval_items (run_id, item_type, item_id, version, status, mandatory, content_json, "
                 "regenerations, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (run_id, item["item_type"], item["item_id"], item["version"] + 1, status, int(item["mandatory"]),
                  json.dumps(content, ensure_ascii=False), regenerations, ts, ts))
    return item["version"] + 1


def _reopen_baseline(conn, run_id) -> bool:
    row = conn.execute("SELECT baseline_status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row and row["baseline_status"] == "frozen":
        conn.execute("UPDATE runs SET baseline_status = 'open' WHERE run_id = ?", (run_id,))
        return True
    return False


def _sources(run) -> list[dict]:
    """The (masked) scenario, interview answers and documents a requirement quote may come from."""
    from app.agents.requirement_agent import source_texts
    r = run["result"]
    return source_texts(r.get("scenario", ""), r.get("answers"), r.get("documents"))


# --- content checks for "modify" -------------------------------------------------

def _validated_requirement(run, current, changes) -> dict:
    unknown = sorted(set(changes) - set(REQUIREMENT_EDITABLE))
    if unknown:
        raise InvalidInput(f"These requirement fields cannot be changed: {unknown}. Editable: {list(REQUIREMENT_EDITABLE)}.")
    merged = {**current["content"], **changes}
    all_ids = [it["item_id"] for it in current_items(run["run_id"]) if it["item_type"] == "requirement"]
    try:
        rec = Requirement.model_validate(merged)
        complete_requirement(rec, "NFR" if rec.id.startswith("NFR") else "FR", all_ids, _sources(run), status="modified")
    except (ValidationError, ValueError) as e:
        raise InvalidInput(f"Modified requirement is not valid: {e}")
    return rec.model_dump()


def _validated_compliance(current, changes) -> dict:
    unknown = sorted(set(changes) - set(COMPLIANCE_EDITABLE))
    if unknown:
        raise InvalidInput(f"These compliance fields cannot be changed: {unknown}. Editable: {list(COMPLIANCE_EDITABLE)}.")
    content = current["content"]
    merged = {"related_requirement_ids": [content["requirement_id"]], "source": content["source"],
              "note": changes.get("note", content.get("required_control") or "-"),
              "status": changes.get("status", content.get("status") if content.get("status") in
                                    ("satisfied", "gap", "not_applicable") else None),
              "compliance_gap": changes.get("compliance_gap", content.get("compliance_gap")),
              "checkpoints": changes.get("checkpoints", content.get("checkpoints", [])),
              "retention_and_reporting": changes.get("retention_and_reporting", content.get("retention_and_reporting", []))}
    try:
        c = Consideration.model_validate(merged)
    except ValidationError as e:
        raise InvalidInput(f"Modified compliance interpretation is not valid: {e}")
    if c.status is None:
        raise InvalidInput("A reviewed compliance interpretation needs a status: satisfied, gap or not_applicable.")
    return {**content, "status": c.status, "required_control": c.note, "compliance_gap": c.compliance_gap,
            "checkpoints": c.checkpoints, "retention_and_reporting": c.retention_and_reporting, "reviewed": True}


def _validated_sdlc(current, changes) -> dict:
    from app.agents.sdlc_rules import SDLC_PROFILES
    if set(changes) != {"recommended_model"} or changes["recommended_model"] not in SDLC_PROFILES:
        raise InvalidInput(f"Modify the SDLC selection with {{\"recommended_model\": one of {list(SDLC_PROFILES)}}}.")
    return {**current["content"], "recommended_model": changes["recommended_model"],
            "human_override": changes["recommended_model"] != current["content"]["recommended_model"]}


# --- regeneration (re-runs one stage with the reviewer's comment) -----------------

def _requirements_of(run) -> tuple[list[dict], list[dict]]:
    current = {it["item_id"]: it["content"] for it in current_items(run["run_id"]) if it["item_type"] == "requirement"}
    frs = [r for i, r in current.items() if i.startswith("FR")]
    nfrs = [r for i, r in current.items() if i.startswith("NFR")]
    return frs, nfrs


def _regenerate(run, current, comment) -> dict:
    from app.agents import compliance_agent, requirement_agent, sdlc_agent
    result, frs, nfrs = run["result"], *_requirements_of(run)
    domain = result["requirements"].get("domain_inferred", "Unknown")
    if current["item_type"] == "requirement":
        return requirement_agent.regenerate_requirement(current["content"], comment, result.get("scenario", ""),
                                                        [r["id"] for r in frs + nfrs], _sources(run))
    if current["item_type"] == "compliance":
        rid, source = current["item_id"].split("/", 1)
        req = [r for r in frs + nfrs if r["id"] == rid]
        out = compliance_agent.analyze_compliance(
            domain, [r for r in req if rid.startswith("FR")], [r for r in req if rid.startswith("NFR")],
            scenario=result.get("scenario"), reviewer_comment=comment)
        entry = next((e for e in out["compliance_mapping"]["requirements"] if e["requirement_id"] == rid), {"mappings": []})
        match = next((m for m in entry["mappings"] if m["source"] == source), None)
        return ({"requirement_id": rid, **match} if match else
                {**current["content"], "status": "not_assessed", "evidence_verified": False, "confidence": "Low",
                 "required_control": "", "note_regeneration": f"{source} was not retrieved for {rid} when regenerated"})
    out = sdlc_agent.recommend_sdlc(domain, frs, nfrs, scenario=result.get("scenario"), reviewer_comment=comment)
    return {"recommended_model": out["recommended_model"], "is_close_call": out["is_close_call"],
            "ranking": [{"model": m["model"], "score": m["score"]} for m in out["scored_models"]],
            "traits": out["traits"]}


# --- public API -------------------------------------------------------------------

def act(run_id: str, item_type: str, item_id: str, action: str, role: str,
        comment: str = "", content: dict | None = None) -> dict:
    run = get_run(run_id)
    if run is None:
        raise NotFound(f"Unknown run {run_id}.")
    if item_type not in ("requirement", "compliance", "sdlc"):
        raise InvalidInput("item_type must be requirement, compliance or sdlc.")
    if action not in ACTIONS:
        raise InvalidInput(f"action must be one of {ACTIONS}.")
    current = _current(run_id, item_type, item_id)
    roles.require(roles.review_permission(item_type, current["content"]), role)
    comment = (comment or "").strip()
    if action != "accept" and not comment:
        raise InvalidInput(f"A comment is required to {action}.")
    if current["status"] == "superseded":
        raise Conflict("Only the current version can be reviewed.")

    reopened = False
    new_version = None
    if action in ("accept", "reject"):
        if current["status"] not in OPEN_STATUSES:
            raise Conflict(f"{item_type} {item_id} v{current['version']} is {current['status']}; "
                           "modify or regenerate it to create a new version first.")
        if item_type == "sdlc" and action == "accept" and role in sdlc_accepts(run_id, current["version"]):
            raise Conflict(f"{role} has already accepted this version.")
    elif action == "regenerate":
        if current["regenerations"] >= MAX_REGENERATIONS:
            raise Conflict(f"{item_type} {item_id} has been regenerated {MAX_REGENERATIONS} times; "
                           "modify it manually instead.")
        try:
            new_content = _regenerate(run, current, comment)
        except Exception as e:
            append_event("approval_regenerate_failed", role, run_id=run_id,
                         details={"item_type": item_type, "item_id": item_id, "comment": comment, "error": str(e)[:2000]})
            raise RegenerationFailed(f"Regeneration failed: {e}")
    else:  # modify
        if not content:
            raise InvalidInput("modify needs the changed fields in 'content'.")
        new_content = (_validated_requirement(run, current, content) if item_type == "requirement" else
                       _validated_compliance(current, content) if item_type == "compliance" else
                       _validated_sdlc(current, content))

    with connect() as conn:
        _record_decision(conn, run_id, current, action, role, comment)
        if action == "accept":
            if item_type == "sdlc":
                accepted = set(sdlc_accepts(run_id, current["version"])) | {role}
                if roles.SDLC_APPROVERS_REQUIRED <= accepted:
                    _set_status(conn, run_id, current, "approved")
            else:
                _set_status(conn, run_id, current, "approved")
        elif action == "reject":
            _set_status(conn, run_id, current, "rejected")
        else:
            status = "modified" if action == "modify" else "proposed"
            regenerations = current["regenerations"] + (action == "regenerate")
            if item_type == "requirement":
                new_content["approval_status"] = status
            new_version = _new_version(conn, run_id, current, new_content, status, regenerations)
            reopened = _reopen_baseline(conn, run_id)
    after = _current(run_id, item_type, item_id)
    details = {"item_type": item_type, "item_id": item_id, "version": current["version"], "comment": comment,
               "status_after": after["status"], "new_version": new_version}
    if action == "modify":
        details["changed_fields"] = sorted(content)
    append_event(f"approval_{action}", role, run_id=run_id, details=details)
    if reopened:
        append_event("baseline_reopened", role, run_id=run_id,
                     details={"reason": f"{item_type} {item_id} changed after the baseline was frozen"})
    return state(run_id, item_type, item_id)


def state(run_id: str, item_type: str, item_id: str) -> dict:
    versions = item_versions(run_id, item_type, item_id)
    if not versions:
        raise NotFound(f"No {item_type} item {item_id!r} in run {run_id}.")
    cur = versions[-1]
    out = {"item_type": item_type, "item_id": item_id, "version": cur["version"], "status": cur["status"],
           "mandatory": cur["mandatory"], "regenerations": cur["regenerations"],
           "regenerations_left": MAX_REGENERATIONS - cur["regenerations"], "content": cur["content"],
           "history": [{"version": v["version"], "status": v["status"]} for v in versions]}
    if item_type == "sdlc":
        accepted = sdlc_accepts(run_id, cur["version"])
        out["accepted_by"] = accepted
        out["awaiting"] = sorted(roles.SDLC_APPROVERS_REQUIRED - set(accepted)) if cur["status"] in OPEN_STATUSES else []
    return out


def blockers(run_id: str) -> list[dict]:
    return [{"item_type": it["item_type"], "item_id": it["item_id"], "version": it["version"], "status": it["status"]}
            for it in current_items(run_id) if it["mandatory"] and it["status"] != "approved"]


def freeze_baseline(run_id: str, role: str) -> dict:
    run = get_run(run_id)
    if run is None:
        raise NotFound(f"Unknown run {run_id}.")
    roles.require("freeze_baseline", role)
    if run["baseline_status"] == "frozen":
        raise Conflict("The baseline is already frozen.")
    waiting = blockers(run_id)
    if waiting:
        append_event("baseline_freeze_blocked", role, run_id=run_id, details={"blockers": waiting})
        raise Conflict({"message": f"{len(waiting)} mandatory item(s) are not approved.", "blockers": waiting})
    snapshot = [{"item_type": it["item_type"], "item_id": it["item_id"], "version": it["version"]}
                for it in current_items(run_id)]
    ts = now()
    with connect() as conn:
        conn.execute("UPDATE runs SET baseline_status = 'frozen', baseline_frozen_at = ?, baseline_frozen_by = ? "
                     "WHERE run_id = ?", (ts, role, run_id))
        conn.execute("INSERT INTO decisions (run_id, item_type, item_id, version, action, role, comment, created_at) "
                     "VALUES (?, 'baseline', 'baseline', 0, 'freeze', ?, ?, ?)",
                     (run_id, role, json.dumps(snapshot), ts))
    append_event("baseline_frozen", role, run_id=run_id, details={"items": snapshot})
    return {"run_id": run_id, "baseline_status": "frozen", "frozen_at": ts, "frozen_by": role, "items": snapshot}
