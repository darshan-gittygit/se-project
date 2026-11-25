# Requora AI - stakeholder satisfaction form

Give this form to each reviewer after they have used Requora AI on one case study (the scenario, the generated requirements, the compliance and risk view, the SDLC recommendation and the SRS). One form per reviewer per case.

Record the answers in a CSV file in `eval/satisfaction/` (copy `eval/satisfaction/TEMPLATE.csv`; one row per form). Use an anonymous respondent id such as `R1`, never a name or email. `python -m scripts.eval_report` summarises all files there.

**Scale:** 1 = strongly disagree, 2 = disagree, 3 = neutral, 4 = agree, 5 = strongly agree. Leave a question blank if it does not apply.

| # | Statement | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| q1 | The generated requirements were correct and relevant to the scenario. | | | | | |
| q2 | The requirements were complete enough to start design (no important gaps). | | | | | |
| q3 | The compliance and risk findings were useful and clearly marked as advisory, not legal advice. | | | | | |
| q4 | The SDLC recommendation and its explanation were convincing. | | | | | |
| q5 | Using the system would save me time compared with my usual requirements process. | | | | | |

**Comment (optional):** what was most useful, and what would you change?

Respondent id: ______  Role: ______________________  Case: ______________________
