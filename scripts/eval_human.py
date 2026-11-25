"""
Human inputs to the evaluation (problem statement section 19: "time saved
compared with manual analysis", "stakeholder satisfaction"). Standard library.

Manual baseline: eval/manual_baseline/<case id>.md, a copy of TEMPLATE.md
filled in by hand. parse_manual() reads it; compare_manual() scores the hand
requirements against the same gold with the same keyword matching as the
system, and compares time.

Satisfaction: eval/satisfaction/*.csv (header in TEMPLATE.csv), one row per
respondent and case, five Likert answers 1-5 for the questions in
eval/satisfaction_form.md. aggregate_satisfaction() summarises them.
"""
import csv
import re
import statistics
from pathlib import Path

from scripts.evaluate import extraction, match_requirements

ROOT = Path(__file__).resolve().parent.parent
MANUAL_DIR = ROOT / "eval" / "manual_baseline"
SATISFACTION_DIR = ROOT / "eval" / "satisfaction"
QUESTIONS = ("q1", "q2", "q3", "q4", "q5")
CSV_HEADER = ("respondent", "role", "case_id", *QUESTIONS, "comment")

_FIELD = re.compile(r"^([A-Za-z][A-Za-z ()/-]*?):\s*(.*)$")
_ITEM = re.compile(r"^\s*[-*]\s+(?:\[([^\]]+)\]\s*)?(.+?)\s*$")


# --- manual baseline ----------------------------------------------------------------

def _minutes(value: str) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", value or "")
    return float(m.group()) if m else None


def parse_manual(path: Path) -> dict:
    """
    Reads a filled-in manual baseline. Header lines are "Key: value"; sections
    start with "## "; list items are "- [Category] text" (category optional).
    """
    fields, sections, current = {}, {}, None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections[current] = []
            continue
        if line.startswith("#") or not line.strip() or line.lstrip().startswith("<!--"):
            continue
        item = _ITEM.match(line)
        if current and item:
            sections[current].append({"category": (item.group(1) or "").strip() or None, "text": item.group(2)})
            continue
        f = _FIELD.match(line.strip())
        if f and current in (None, "sdlc choice"):   # header fields, and "Model:" / "Reason:" under SDLC choice
            key = f.group(1).strip().lower()
            fields[f"sdlc {key}" if current == "sdlc choice" else key] = f.group(2).strip()
    reqs = [{"id": f"M-{i + 1}", "requirement": it["text"], "categories": [it["category"]] if it["category"] else []}
            for i, it in enumerate(sections.get("requirements", []))]
    return {"case_id": fields.get("case", Path(path).stem), "analyst": fields.get("analyst", ""),
            "minutes": _minutes(fields.get("time taken (minutes)", "")),
            "review_minutes": _minutes(fields.get("time reviewing the system output (minutes)", "")),
            "requirements": reqs, "ambiguities": [x["text"] for x in sections.get("ambiguities found", [])],
            "conflicts": [x["text"] for x in sections.get("conflicts found", [])],
            "sdlc_model": fields.get("sdlc model", "") or None}


def load_manual(manual_dir: Path = MANUAL_DIR) -> dict[str, dict]:
    """Filled-in baselines by case id (TEMPLATE.md and files without requirements are skipped)."""
    out = {}
    for p in sorted(Path(manual_dir).glob("*.md")):
        if p.name.upper() == "TEMPLATE.MD":
            continue
        m = parse_manual(p)
        if m["requirements"]:
            out[m["case_id"]] = m
    return out


def compare_manual(case: dict, manual: dict, system_metrics: dict | None) -> dict:
    """Hand-written requirements scored against the gold like the system's, plus the time comparison."""
    gold = case["gold"]["requirements"]
    matching = match_requirements(gold, manual["requirements"])
    ext = extraction(gold, manual["requirements"], matching)
    system_seconds = (system_metrics or {}).get("processing_seconds")
    system_minutes = system_seconds / 60 if system_seconds is not None else None
    review = manual.get("review_minutes")
    assisted = system_minutes + review if system_minutes is not None and review is not None else None
    sys_ext = (system_metrics or {}).get("extraction", {})
    return {"case_id": case["id"], "manual": {"precision": ext["precision"], "recall": ext["recall"], "f1": ext["f1"],
                                              "count": ext["system_count"], "missed_gold_ids": ext["missed_gold_ids"]},
            "system": {"precision": sys_ext.get("precision"), "recall": sys_ext.get("recall"), "f1": sys_ext.get("f1"),
                       "count": sys_ext.get("system_count")},
            "manual_minutes": manual.get("minutes"), "system_minutes": system_minutes, "review_minutes": review,
            "assisted_minutes": assisted,
            "time_saved_minutes": (manual["minutes"] - assisted) if assisted is not None and manual.get("minutes") is not None else None,
            "manual_sdlc": manual.get("sdlc_model"), "gold_sdlc": case["gold"]["sdlc"]["top"],
            "manual_ambiguities": len(manual.get("ambiguities", [])), "manual_conflicts": len(manual.get("conflicts", []))}


# --- satisfaction -------------------------------------------------------------------

def load_satisfaction(sat_dir: Path = SATISFACTION_DIR) -> list[dict]:
    """All response rows from *.csv (except TEMPLATE.csv). Raises ValueError naming the file and row on bad input."""
    rows = []
    for p in sorted(Path(sat_dir).glob("*.csv")):
        if p.name.upper() == "TEMPLATE.CSV":
            continue
        with p.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            missing = [c for c in CSV_HEADER[:-1] if c not in (reader.fieldnames or [])]
            if missing:
                raise ValueError(f"{p.name}: missing column(s) {', '.join(missing)}; see eval/satisfaction/TEMPLATE.csv")
            for n, row in enumerate(reader, start=2):
                if not any((v or "").strip() for v in row.values()):
                    continue
                parsed = {"respondent": row["respondent"].strip(), "role": row["role"].strip(), "case_id": row["case_id"].strip(),
                          "comment": (row.get("comment") or "").strip()}
                for q in QUESTIONS:
                    v = (row[q] or "").strip()
                    if v == "":
                        parsed[q] = None
                        continue
                    if not v.isdigit() or not 1 <= int(v) <= 5:
                        raise ValueError(f"{p.name} line {n}: {q} must be 1-5 or empty, got {v!r}")
                    parsed[q] = int(v)
                rows.append(parsed)
    return rows


def aggregate_satisfaction(rows: list[dict]) -> dict:
    per_q = {}
    for q in QUESTIONS:
        vals = [r[q] for r in rows if r.get(q) is not None]
        per_q[q] = {"n": len(vals), "mean": float(statistics.mean(vals)) if vals else None,
                    "median": float(statistics.median(vals)) if vals else None,
                    "distribution": {str(k): vals.count(k) for k in range(1, 6)},
                    "agree_share": (sum(v >= 4 for v in vals) / len(vals)) if vals else None}
    every = [r[q] for r in rows for q in QUESTIONS if r.get(q) is not None]
    return {"responses": len(rows), "respondents": len({r["respondent"] for r in rows}),
            "roles": sorted({r["role"] for r in rows if r["role"]}), "questions": per_q,
            "overall_mean": float(statistics.mean(every)) if every else None}
