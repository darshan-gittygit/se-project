# Requora AI - Problem Statement Coverage Plan

Tracks every numbered section of `docs/PROBLEM_STATEMENT.md` against a concrete deliverable in the prototype. Update the Status column as work lands. "Simplified" means a working demo version of something that would be a full platform in production; the report must say so honestly.

Status key: DONE, PARTIAL, TODO. Phases are in `docs/ROADMAP` order below.

## Section coverage

| # | Section | Deliverable in the prototype | Level | Phase | Status |
|---|---|---|---|---|---|
| 1 | Define system scope | Supported financial use cases listed in-app (digital banking, loan origination, payments, fraud detection, insurance/investment, regulatory reporting, KYC onboarding, analytics); scope page + list of outputs (SRS, user stories, use cases, acceptance criteria, compliance mappings, risk register, traceability matrix, SDLC) | Full | 10 | TODO |
| 2 | Identify stakeholders | 9 stakeholder roles in the data model; role-specific interview templates (JSON/Markdown), selectable in the interview UI | Full | 2 | PARTIAL (9 roles in `app/vocabulary.py`; 27 role-specific templates, 3 per role, as Python data in `app/agents/interview_templates.py`, served by `GET /agents/interview/templates`; not yet selectable in a UI) |
| 3 | Input sources | Text/paste, .txt/.md/.pdf/.docx upload for transcripts, notes, policies, requirement docs, API/DB specs, legacy docs, incident reports; sensitive-data classification and masking before any LLM call | Full for text-based files | 7 | TODO |
| 4 | Multi-agent architecture | Coordinator/orchestrator plus the 13 named agents as separate modules with defined inputs/outputs, shared context and approval gates (some agents may share one LLM call; each remains a distinct module and appears in the audit trail) | Full | 1-6 | PARTIAL (requirements, interview, quality/conflict, compliance + mapping, risk register, SDLC exist; no coordinator, shared context or approval gates) |
| 5 | Financial knowledge base | Versioned KB with per-item source, jurisdiction, effective date, version, applicability; categories: terminology, policies, regulations, security standards, RE guidelines, SDLC rules, templates, approved docs. Paraphrased principles with sources, never invented law | Full (small curated set) | 3 | PARTIAL (31 chunks of paraphrased principles; per-source metadata in `knowledge_base/sources.json`: source, jurisdiction, version and effective date of the KB entry, and the domains it applies to; no terminology, policies, RE guidelines, SDLC rules or templates; versioning is git history only) |
| 6 | Retrieval-grounded generation | Classify query, retrieve, cite, confidence score, escalate low-confidence or unsupported output to human review | Full | 3 | PARTIAL (rule-based domain classification of the requirements, per-requirement retrieval, tuned threshold, only sources that apply to the domain can be cited or count, confidence graded from distance and verified evidence, `needs_human_review` with reasons; review is a flag, not yet a workflow) |
| 7 | Requirement-gathering conversations | Adaptive interviewer agent that asks follow-ups on vague or incomplete answers across the listed topic areas | Full | 2 | PARTIAL (`POST /agents/interview`: follow-ups on vague, incomplete or inconsistent answers plus template questions for uncovered topics; coverage computed from answers; API only) |
| 8 | Extract and structure requirements | 13-field requirement record: ID, statement, category, source stakeholder, justification, priority, dependencies, assumptions, acceptance criteria, applicable regulations, risk level, confidence, approval status; domain-style IDs (e.g. FR-PAY-001) | Full | 1 | PARTIAL (all 13 fields plus type and source_text; confidence is a computed High/Medium/Low grade; approval_status always "proposed"; IDs are still FR-n / NFR-n, not domain-style) |
| 9 | Classify requirements | Multi-label classification into the 13 listed categories | Full | 1 | DONE (one or more of the 13 categories per requirement, schema-checked; accuracy not yet measured) |
| 10 | Analyse requirement quality | Checks: ambiguity, incompleteness, inconsistency, duplication, infeasibility, testability, missing source, undefined terms, conflicting stakeholder expectations, missing security/compliance controls; failures return to the clarification agent | Full | 2 | PARTIAL (`POST /agents/quality`: ambiguity and duplication by rules, testability for Performance and Availability-and-reliability NFRs, inconsistency/conflicts by one LLM call; no incompleteness, infeasibility, undefined-term, stakeholder-conflict or missing-control checks; failures not yet routed back to the interview) |
| 11 | Compliance and security analysis | Per-requirement mapping: jurisdiction/regulation, required control, evidence, gaps, approval/audit checkpoints, retention/reporting obligations; explicit "not a legal determination" notice and approval routing | Full | 3 | PARTIAL (per-requirement `compliance_mapping`: regulation, jurisdiction, version, required control, verified evidence, satisfied/gap, approval or audit checkpoints, retention and reporting obligations, graded confidence; "not a legal determination" notice; gaps on high-risk requirements flagged for compliance/legal review; flags only, no approval routing) |
| 12 | Generate artefacts | SRS, user stories + acceptance criteria, use cases, process workflows, data requirements, interface requirements, threat and risk register, compliance-control matrix, Requirements Traceability Matrix, assumptions/dependency register, open-issues list; each linked to source statement; export to Word/PDF/Markdown | Full | 5 | PARTIAL (threat and risk register with likelihood x impact scoring and per-requirement compliance mapping in `/agents/pipeline`; acceptance criteria, assumptions, dependencies and source-statement links per requirement; no SRS, user stories, use cases, RTM, open-issues list or export) |
| 13 | Extract SDLC decision factors | The 13 listed factors (requirement stability, regulatory criticality, security risk, complexity, size, legacy dependence, change frequency, continuous-delivery need, stakeholder availability, testing/documentation needs, budget/schedule, formal-verification need, failure consequences) with evidence per factor | Full | 4 | PARTIAL (9 traits) |
| 14 | SDLC selection engine | Hybrid rules + multi-criteria scoring + LLM explanation; models: Waterfall, V-Model, Incremental, Agile, Spiral, DevSecOps, Agile-V hybrid, Agile-DevSecOps hybrid; ranked percentages; approval by PM, architect, security, compliance | Full | 4 | PARTIAL (5 models, ranked, tested) |
| 15 | Project-specific SDLC workflow | Generated phases, activities, roles, deliverables, security activities, testing, compliance checkpoints, approval gates, entry/exit criteria, traceability requirements | Full | 4 | TODO |
| 16 | Human-in-the-loop | Mandatory approval gates for baselines, regulatory interpretations, high-risk security requirements, conflicts, architecture-critical items, SDLC selection, changes to approved requirements, production readiness; accept / reject / modify / regenerate | Full | 6 | TODO |
| 17 | Secure the platform | RBAC with roles and agent-level permissions, session isolation, sensitive-data masking, prompt/output filtering, prompt-injection defence, retrieval-source allowlist, audit logging, retention policy, model/KB versioning, encryption (TLS note + at-rest for stored data) | Simplified: MFA and enterprise IAM as demo/TOTP or documented design | 6 | PARTIAL (retrieval-source allowlist for citations, schema validation of every LLM output, input size limits, unauthenticated test route removed; no RBAC, masking, injection defence or audit log) |
| 18 | Develop the prototype | LLM + orchestration + vector DB (ChromaDB) + relational DB (SQLite) + rule engine + approval state machine + web UI + document generation + auth + audit/monitoring view; begin with loan processing as the flagship use case | Full | all | PARTIAL |
| 19 | Test and evaluate | Evaluation harness over financial case studies (digital banking, loan processing, payments, fraud detection, insurance, regulatory reporting) with metrics: extraction precision/recall/F1, completeness, ambiguity-detection accuracy, conflict-detection accuracy, regulatory-control coverage, hallucination rate, citation correctness, SDLC accuracy vs expert decisions, human correction rate, time saved vs manual, stakeholder satisfaction (survey form), traceability coverage; comparison with a manual baseline | Full, on small hand-labelled case-study set | 8 | TODO |
| 20 | Deploy, monitor and improve | Advisory-mode switch, comparison view vs analyst output, human-approved generation, monitoring of incorrect recommendations and compliance gaps, KB/rule update workflow, re-evaluation after updates; full audit record of prompts, evidence, decisions, changes, approvals, artefacts | Simplified (monitoring dashboard + documented rollout plan) | 6, 8 | TODO |

## Research challenges named in the statement

| Challenge | How the prototype addresses it |
|---|---|
| LLM hallucination | Retrieval grounding, citation correctness check, "no coverage" responses, hallucination-rate metric |
| Explainability | Evidence per trait/factor, deterministic scoring, why-not explanations |
| Sensitive financial data | Classification and masking before LLM calls, minimal-data agent inputs |
| Bias | Fairness test for the SDLC engine (no per-model bonus; win share is unequal and documented in `docs/SCORING_ANALYSIS.md`), tie-break rules, reporting of close calls |
| Prompt injection | Input/output filters, delimiting of untrusted text, allowlisted retrieval sources |
| Access control | RBAC, agent-level permissions, session isolation |
| Regulatory change | Versioned KB with effective dates, re-evaluation workflow |
| Interoperability | JSON/REST APIs, document export formats, import of API/DB specs |
| Human oversight | Approval gates and accept/reject/modify/regenerate controls |

## Roadmap

0. Evaluate the current state and fix critical findings.
1. Data model (SQLite) + coordinator + enriched requirement records + multi-label classification.
2. Stakeholder interview templates, adaptive interviewer, quality checks, clarification and conflict detection, validation agent.
3. Knowledge base upgrade (metadata, versioning), compliance mapping, risk register, confidence scoring, escalation.
4. SDLC: extra factors, DevSecOps and hybrid models, ranked results, generated workflow.
5. Artefact generation and export (SRS, user stories, use cases, RTM, registers, open issues).
6. Human approval gates, audit log, masking, injection defence, RBAC, monitoring view.
7. File upload and input-source handling.
8. Evaluation harness, case studies, metrics, manual baseline comparison.
9. UI rebuild (violet-on-black design) wired to all features.
10. Scope page, report, UML diagrams, viva and demo preparation.

Rule for every phase: tests pass, live scenarios still pick sensible models, a commit is made, and the Status column above is updated.
