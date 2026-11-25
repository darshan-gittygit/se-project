"""
Pipeline runs and their approvable items, stored in SQLite (app/db.py).

save_run() stores the full /agents/pipeline result exactly as generated (it
is never edited afterwards - it is the record of what the system produced)
and registers version 1 of every item that needs human approval
(problem statement section 16):

- requirement     each FR/NFR                          item_id "FR-1"
- compliance      each domain-specific knowledge-base   item_id "FR-1/kyc_aml"
                  mapping (a regulatory interpretation)
- sdlc            the SDLC selection                    item_id "sdlc"

All three kinds are mandatory for freezing the baseline. Later versions are
created by approval actions (app/approvals.py). with_current_state() lays the
current versions and statuses over the stored result, so the SRS shows the
real approval state.
"""
import copy
import json
import uuid

from app import config
from app.audit import now
from app.db import connect
from app.rag.kb_metadata import kb_version

ITEM_TYPES = ("requirement", "compliance", "sdlc")


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def initial_items(result: dict) -> list[tuple[str, str, dict]]:
    req = result.get("requirements", {})
    items = [("requirement", r["id"], r) for r in req.get("functional_requirements", []) + req.get("non_functional_requirements", [])]
    for e in (result.get("compliance_mapping") or {}).get("requirements", []):
        for m in e["mappings"]:
            if m.get("scope") == "domain-specific":
                items.append(("compliance", f"{e['requirement_id']}/{m['source']}", {"requirement_id": e["requirement_id"], **m}))
    sdlc = result.get("sdlc_recommendation")
    if sdlc:
        items.append(("sdlc", "sdlc", {
            "recommended_model": sdlc["recommended_model"], "is_close_call": sdlc.get("is_close_call", False),
            "ranking": [{"model": m["model"], "score": m["score"]} for m in sdlc.get("scored_models", [])]}))
    return items


def save_run(run_id: str, result: dict, role: str) -> None:
    ts = now()
    with connect() as conn:
        conn.execute("INSERT INTO runs (run_id, created_at, role, app_version, kb_version, scenario, result_json) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (run_id, ts, role, config.APP_VERSION, kb_version(), result.get("scenario", ""),
                      json.dumps(result, ensure_ascii=False)))
        for item_type, item_id, content in initial_items(result):
            conn.execute("INSERT INTO approval_items (run_id, item_type, item_id, version, status, mandatory, "
                         "content_json, regenerations, created_at, updated_at) VALUES (?, ?, ?, 1, 'proposed', 1, ?, 0, ?, ?)",
                         (run_id, item_type, item_id, json.dumps(content, ensure_ascii=False), ts, ts))


def get_run(run_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    run = dict(row)
    run["result"] = json.loads(run.pop("result_json"))
    return run


def _item(row) -> dict:
    d = dict(row)
    d["content"] = json.loads(d.pop("content_json"))
    d["mandatory"] = bool(d["mandatory"])
    return d


def item_versions(run_id: str, item_type: str | None = None, item_id: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM approval_items WHERE run_id = ?", [run_id]
    if item_type:
        sql += " AND item_type = ?"; params.append(item_type)
    if item_id:
        sql += " AND item_id = ?"; params.append(item_id)
    with connect() as conn:
        return [_item(r) for r in conn.execute(sql + " ORDER BY item_type, item_id, version", params)]


def current_items(run_id: str) -> list[dict]:
    """The latest version of every item, in registration order of type."""
    latest: dict[tuple, dict] = {}
    for it in item_versions(run_id):
        latest[(it["item_type"], it["item_id"])] = it
    order = {t: i for i, t in enumerate(ITEM_TYPES)}
    return sorted(latest.values(), key=lambda it: (order[it["item_type"]], it["item_id"]))


def decisions(run_id: str) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM decisions WHERE run_id = ? ORDER BY id", (run_id,))]


def with_current_state(run: dict) -> dict:
    """A copy of the stored result with current versions, statuses and the baseline state applied."""
    result = copy.deepcopy(run["result"])
    items = current_items(run["run_id"])
    reqs = {it["item_id"]: it for it in items if it["item_type"] == "requirement"}
    for key in ("functional_requirements", "non_functional_requirements"):
        result["requirements"][key] = [
            {**reqs[r["id"]]["content"], "approval_status": reqs[r["id"]]["status"]} if r["id"] in reqs else r
            for r in result["requirements"].get(key, [])]
    sdlc_item = next((it for it in items if it["item_type"] == "sdlc"), None)
    if sdlc_item and result.get("sdlc_recommendation"):
        approval = result["sdlc_recommendation"].setdefault("approval_required", {})
        approval["status"] = "pending" if sdlc_item["status"] in ("proposed", "modified") else sdlc_item["status"]
        approval["approved_by"] = sdlc_accepts(run["run_id"], sdlc_item["version"])
        if sdlc_item["content"].get("human_override"):
            result["sdlc_recommendation"]["human_override"] = sdlc_item["content"]["recommended_model"]
    comp = [it for it in items if it["item_type"] == "compliance"]
    approved = sum(it["status"] == "approved" for it in comp)
    result["approval_summary"] = {
        "compliance": ("approved" if comp and approved == len(comp) else f"pending ({approved} of {len(comp)} approved)"),
        "items": {f"{it['item_type']}:{it['item_id']}": {"version": it["version"], "status": it["status"]} for it in items},
    }
    result["baseline"] = {"status": run["baseline_status"], "frozen_at": run["baseline_frozen_at"],
                          "frozen_by": run["baseline_frozen_by"]}
    return result


def sdlc_accepts(run_id: str, version: int) -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT DISTINCT role FROM decisions WHERE run_id = ? AND item_type = 'sdlc' "
                            "AND item_id = 'sdlc' AND version = ? AND action = 'accept' ORDER BY role", (run_id, version))
        return [r["role"] for r in rows]
