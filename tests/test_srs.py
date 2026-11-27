"""Phase 4 step 3: SRS assembly and rendering. Offline, no LLM."""
import re

from app.srs import NOTICE, build_blocks, render_html, render_markdown
from tests.artefact_fixtures import REQUIREMENTS, pipeline_result

WHEN = "2026-11-26 10:00 UTC"
SECTIONS = ["1. Introduction", "2. Stakeholders and sources", "3. Functional requirements", "4. Non-functional requirements",
            "5. User stories", "6. Use cases", "7. Process workflows", "8. Data requirements", "9. Interface requirements",
            "10. Compliance-control matrix", "11. Threat and risk register", "12. SDLC recommendation",
            "13. Development workflow (V-Model)", "14. Requirements traceability matrix", "15. Assumptions and dependencies",
            "16. Open issues", "17. Approval status"]


def md(result=None):
    return render_markdown(build_blocks(result or pipeline_result(), generated_at=WHEN))


def test_markdown_has_every_section_in_order():
    headings = [l[3:] for l in md().splitlines() if l.startswith("## ")]
    assert headings == SECTIONS


def test_notice_appears_at_the_top_and_with_the_approvals():
    text = md()
    assert text.count(f"> **{NOTICE}**") == 2
    assert text.index(NOTICE) < text.index("## 1. Introduction")
    assert "> **Not a legal determination." in text.split("## 10. Compliance-control matrix")[1]


def test_approval_table_is_all_proposed_or_pending():
    section = md().split("## 17. Approval status")[1]
    rows = [l for l in section.splitlines() if l.startswith("| ") and not l.startswith("| Item")]
    assert len(rows) == 3 + 4  # three requirements, SDLC model, compliance review, artefacts, workflow
    assert all(("| proposed |" in r) or ("| pending |" in r) for r in rows)
    assert "| SDLC model: V-Model | pending | Project manager, Architect, Security team, Compliance officer |" in section


def test_requirement_rows_and_generated_items_keep_their_stakeholder_statement():
    text = md()
    assert '| FR-1 | The system shall verify applicant identity documents. |' in text
    assert 'Compliance and legal officers: "upload identity and address documents for KYC verification"' in text
    assert "Software architects and developers: no stakeholder statement; assumed: 5 seconds is acceptable \\| to applicants" in text
    story_row = next(l for l in text.splitlines() if l.startswith("| US-1 |"))
    assert "As an applicant, I want to upload my documents" in story_row
    # the fixture's generated items carry no source_statements; the RTM still links them back
    rtm = text.split("## 14. Requirements traceability matrix")[1].split("## 15.")[0]
    assert '| FR-1 | Compliance and legal officers: "upload identity and address documents for KYC verification" |' in rtm
    assert "| FR-1 |" in rtm and "US-1" in rtm and "RISK-2" in rtm


def test_table_cells_escape_pipes_and_flatten_newlines():
    result = pipeline_result()
    result["requirements"]["functional_requirements"][0]["requirement"] = "Line one\nline | two"
    text = md(result)
    assert "| FR-1 | Line one line \\| two |" in text


def test_workflow_scenario_items_are_marked():
    assert "Decline-reason tests (added for FR-2)" in md()


def test_missing_parts_render_without_errors():
    minimal = {"scenario": "x", "requirements": REQUIREMENTS}
    text = md(minimal)
    assert [l[3:] for l in text.splitlines() if l.startswith("## ")][:5] == SECTIONS[:5]
    assert "None generated." in text and "No register." in text and "## 12. SDLC recommendation\n\nNot available." in text


def test_html_is_escaped_self_contained_and_print_friendly():
    result = pipeline_result()
    result["scenario"] = "<script>alert(1)</script> & loans"
    page = render_html(build_blocks(result, generated_at=WHEN))
    assert page.startswith("<!DOCTYPE html>") and "<title>Software Requirements Specification: Digital Loan Origination</title>" in page
    assert "<script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt; &amp; loans" in page
    assert "@media print" in page and 'onclick="window.print()"' in page
    assert not re.search(r'(src|href)=["\']https?://', page)  # no external resources
    assert page.count("<h2>") == len(SECTIONS) and "<table>" in page and '<div class="note">' in page


def test_markdown_and_html_come_from_the_same_blocks():
    blocks = build_blocks(pipeline_result(), generated_at=WHEN)
    md_rows = render_markdown(blocks).count("\n| ") - sum(1 for b in blocks if b[0] == "table" and b[2])
    html_rows = render_html(blocks).count("<tr>") - sum(1 for b in blocks if b[0] == "table" and b[2])
    assert md_rows == html_rows


# --- use-case steps are numbered once (the model often numbers them itself) ------------

def _with_numbered_steps():
    r = pipeline_result()
    r["artefacts"]["use_cases"][0]["main_flow"] = ["1. Officer declines", "Step 2: System sends reason", "3) Applicant is told",
                                                   "1.5 seconds later the case closes"]
    return r


def test_use_case_steps_render_once_numbered_in_markdown():
    section = md(_with_numbered_steps()).split("## 6. Use cases")[1].split("## 7.")[0]
    assert "1. Officer declines\n2. System sends reason\n3. Applicant is told\n4. 1.5 seconds later the case closes" in section
    assert "- 1." not in section and "1. 1." not in section


def test_use_case_steps_render_once_numbered_in_html():
    page = render_html(build_blocks(_with_numbered_steps(), generated_at=WHEN))
    assert ("<ol><li>Officer declines</li><li>System sends reason</li><li>Applicant is told</li>"
            "<li>1.5 seconds later the case closes</li></ol>") in page


def test_step_text_strips_only_a_leading_step_number():
    from app.srs import step_text
    assert step_text("1. Officer declines") == "Officer declines"
    assert step_text("  Step 12:  Close the case ") == "Close the case"
    assert step_text("2) Notify") == "Notify"
    assert step_text("1.5 seconds later") == "1.5 seconds later"
    assert step_text("3 approvers sign off") == "3 approvers sign off"
    assert step_text("1.") == "1."                                     # nothing left: keep the text


# --- review counts: requirements and their mappings, named separately ----------------------

def test_review_counts_name_requirements_and_mappings():
    from app.srs import review_counts
    mapping = {"requirements": [
        {"requirement_id": "FR-1", "needs_human_review": True, "mappings": [{"source": "a"}, {"source": "b"}]},
        {"requirement_id": "FR-2", "needs_human_review": False, "mappings": [{"source": "a"}]},
        {"requirement_id": "FR-3", "needs_human_review": True, "mappings": []}]}
    assert review_counts(mapping) == (2, 2)
    assert review_counts({}) == (0, 0)
    r = pipeline_result()
    r["compliance_mapping"] = dict(mapping, review_count=2)
    r.pop("approval_summary", None)
    assert "| Compliance mappings flagged for review: 2 requirements (2 mappings) | pending |" in md(r)
