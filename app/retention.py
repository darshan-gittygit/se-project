"""
Data retention (problem statement section 17: "data-retention policies").

    python -m app.retention                 # uses RETENTION_DAYS from .env / the environment
    python -m app.retention --days 365      # explicit
    python -m app.retention --days 365 --dry-run
    python -m app.retention --days 365 --include-frozen

Deletes stored pipeline runs older than the cutoff - the full result JSON
(masked scenario, answers, documents, requirements, artefacts) and their
approval items. Runs with a frozen baseline are kept unless --include-frozen
is given, because a frozen baseline is the approved record of a project.

What it does NOT delete, by design: the `decisions` and `audit_events`
tables are append-only (database triggers refuse UPDATE and DELETE) and the
audit log is a hash chain. Deleting rows would need the triggers removed and
would break the chain, so those tables keep their rows - including masked
stage outputs and reviewer comments - for as long as the database exists.
The purge itself is recorded as an audit event. See docs/ARCHITECTURE.md
("Retention") for the policy and the options for erasing audit content.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

from app import config
from app.audit import append_event
from app.db import connect


def purge(days: int | None = None, dry_run: bool = False, include_frozen: bool = False,
          now: datetime | None = None) -> dict:
    days = days if days is not None else config.retention_days()
    if not days or days < 1:
        raise ValueError("Give --days N or set RETENTION_DAYS to a positive number of days.")
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat(timespec="microseconds")
    with connect() as conn:
        old = [dict(r) for r in conn.execute(
            "SELECT run_id, baseline_status FROM runs WHERE created_at < ? ORDER BY created_at", (cutoff,))]
        purge_ids = [r["run_id"] for r in old if include_frozen or r["baseline_status"] != "frozen"]
        kept_frozen = [r["run_id"] for r in old if r["run_id"] not in purge_ids]
        if purge_ids and not dry_run:
            marks = ",".join("?" * len(purge_ids))
            conn.execute(f"DELETE FROM approval_items WHERE run_id IN ({marks})", purge_ids)
            conn.execute(f"DELETE FROM runs WHERE run_id IN ({marks})", purge_ids)
    result = {"cutoff": cutoff, "days": days, "dry_run": dry_run, "purged_run_ids": purge_ids,
              "kept_frozen_run_ids": kept_frozen,
              "kept_by_design": "decisions and audit events (append-only, hash-chained)"}
    if not dry_run:
        append_event("runs_purged", "system", details={k: v for k, v in result.items() if k != "dry_run"})
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Delete stored pipeline runs older than the retention period.")
    ap.add_argument("--days", type=int, help="retention period in days (default: RETENTION_DAYS)")
    ap.add_argument("--dry-run", action="store_true", help="list what would be deleted without deleting")
    ap.add_argument("--include-frozen", action="store_true", help="also delete runs whose baseline is frozen")
    args = ap.parse_args(argv)
    try:
        out = purge(args.days, args.dry_run, args.include_frozen)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    verb = "Would delete" if args.dry_run else "Deleted"
    print(f"{verb} {len(out['purged_run_ids'])} run(s) created before {out['cutoff']}; "
          f"kept {len(out['kept_frozen_run_ids'])} frozen baseline(s). "
          "Decisions and audit events are kept (append-only).")
    for rid in out["purged_run_ids"]:
        print(f"  {rid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
