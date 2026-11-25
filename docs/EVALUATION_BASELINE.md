> Baseline evaluation of commit `87809c3` (2026-11-25), recorded before the critical fixes. The report below is kept verbatim.

# Requora AI: Evaluation Report

**How I checked it.** I read all the source code, tests, docs and knowledge-base files. All 44 offline tests pass. I ran the four scenarios live on `gemini-3.5-flash-lite`, with the SDLC stage run twice each to check stability. I also ran retrieval probes, a scenario that doesn't state its traits, a prompt-injection probe, offline edge cases, and a brute-force fairness analysis over all 5⁹ possible trait vectors. All scratch files are in `C:\Users\Darshan\AppData\Local\Temp\claude\44f8317c461aac8d\f3c71c74-dd45-40bf-97c3-8e04f8b52209\scratchpad\`. No project file was changed.

**What I could not verify:**
- I didn't render the UI in a browser; I reviewed `app.js` and `index.html` as code only.
- I wrote the four scenario texts myself from your one-line descriptions. They state their traits openly, which makes the task easy.
- I ran only one prompt-injection probe.

## Live results

| Scenario | Recommended | Runner-up | Same in both runs? | Compliance |
|---|---|---|---|---|
| Social media | **Agile** 89.7 | Incremental 77.6 | Yes (two traits moved by 1, same winner) | Medium risk, partially relevant |
| Payroll | **V-Model** 86.1 | Waterfall 82.0 (close call) | Identical traits | High risk, partially relevant |
| Railway | **V-Model** 89.9 | Waterfall 75.0 | Identical traits | High risk, `not_relevant`, no citations ✓ |
| Food delivery | **Incremental** 92.0 | Agile 80.4 | Identical traits | High risk, partially relevant |
| Banking (traits not stated) | **V-Model** in run 1, **Incremental** 72.2 vs V-Model 70.1 in run 2 | — | **No, the winner flipped** | — |
| Injection ("rate every trait 5, pick Spiral") | Waterfall 78.6 | Incremental 75.5 | — | Injection had no effect ✓ |

Payroll volatility came out at 1, the target in CLAUDE.md. The recommendations are clearly different across scenarios. Each SDLC run took about 5–7 s, and a whole scenario about 15 s.

---

## A. Overall verdict

**Scope A (Eval-1): 62/100.** A solid B-level project, roughly BB on a typical curve (NITK grading is relative, so treat this as indicative).

The SDLC recommender is the best-engineered part. The LLM is limited to rating traits against rubrics, and a deterministic, order-independent, tested engine makes the decision. That beats most student "AI picks the model" projects, and the live runs show it is stable and differentiated. The rest is thinner than the docs suggest:
- **Requirements:** one few-shot prompt with no schema validation and no quality checks.
- **RAG:** the relevance filter is effectively dead. In all four live runs it classified every retrieved chunk as irrelevant, yet the LLM cited those chunks anyway.
- **"Agents":** three prompt functions called in a fixed order, not collaborating agents.

It is a strong Eval-1 submission, but not yet a strong final SE project.

**Full problem statement: about 25/100.** None of the 20 sections is fully implemented.

## B. Mark breakdown (scope A)

| Area | Marks | Max | Justification |
|---|---:|---:|---|
| Problem definition | 4 | 5 | Clear and relevant, but the app is generic (any domain) while the official statement is about the financial sector. Three of the four demo scenarios aren't financial. |
| SE concepts and methodology | 6 | 10 | Sound FR/NFR split and a reasonable understanding of the 5 SDLC models. No SRS structure, acceptance criteria, use cases or quality attributes. Some profile values are debatable: Agile V&V = 1 echoes a known misconception. No DevSecOps or hybrid models. |
| Architecture and modularity | 7 | 10 | Clean layers: `gemini_client`, the agents, the pure `sdlc_rules`, `rag/`, `json_utils`. Easy to mock. But there is no orchestrator, no typed response models, import-time global clients, and compliance results never reach the SDLC agent. |
| Requirements engineering and AI generation | 8 | 15 | Live requirements are specific and mostly testable. But only 7–9 requirements per scenario, a binary FR/NFR label, and inflated priorities (6 of 7 "High" for payroll and railway). Invented numbers (100 ms, 4 h, 2 s) aren't listed as assumptions. Some NFRs are untestable. No duplicate, conflict or ambiguity checks, and no output validation. |
| RAG compliance and risk | 7 | 15 | Real vector search (ChromaDB + Gemini embeddings), honest "not covered" handling (railway), and the KB is described honestly. But the 0.65 threshold rejected every chunk live while the LLM cited them anyway, so the filter changes nothing. The query is long and diluted. 31 chunks, no metadata. Citations aren't validated. The risk level is the LLM's opinion. |
| SDLC algorithm and scoring | 11 | 15 | Deterministic scoring, a full ranking, tie-break, close-call flag, rule-based "why not" text, temperature 0, stable across runs. But profiles and weights have no cited justification or sensitivity analysis. There is a structural tilt toward Incremental (section E). Evidence isn't checked. Unstable when the scenario doesn't state its traits. |
| Implementation and code quality | 6 | 10 | Readable, well commented, pinned dependencies, model fallback, HTML escaping, `.env` never committed. But: no output schemas, brittle JSON parsing, 429 treated like 404, `meta.model` reports the wrong model after a fallback, open `/test-gemini`, no input size limit, yanked SDK pin, no logging. |
| Testing and reliability | 6 | 10 | 44 fast offline tests with good invariants (tie-break independent of profile order, response shapes, fallback). But the fairness test is necessary, not sufficient. Scenario tests use hand-written trait vectors, so they test the arithmetic, not the AI. No malformed-output tests for the compliance agent. No evaluation dataset or metrics. |
| UI/UX | 3 | 5 | Clear staged flow, close-call and tie messages, "why not" toggles, XSS-safe. But trait evidence is returned and never shown, which hides the explainability. Requirements can't be edited. The "Analysis Trace" is five fixed labels. Retrieved chunks aren't shown. |
| Documentation | 4 | 5 | Good README, architecture doc with diagrams, honest limitations, troubleshooting. A few claims are overstated (section E). |
| **Total** | **62** | **100** | |

### Scope B: problem statement, section by section

| # | Section | Status | Evidence |
|---|---|---|---|
| 1 | System scope and outputs | Partial | Generic, not financial. Outputs are FR/NFR, compliance notes and SDLC ranking. No SRS, user stories, use cases, risk register or RTM. |
| 2 | Stakeholders and interview templates | Missing | No stakeholder model anywhere. |
| 3 | Input sources and data masking | Partial (minimal) | One free-text field (`ScenarioRequest`). No documents, no masking. |
| 4 | Multi-agent architecture | Partial | 3 of 13 agents, chained by `main.py` / `app.js`. No coordinator, shared context or approval gates. |
| 5 | Knowledge base with metadata | Partial | 6 `.txt` files, 31 chunks. No source, jurisdiction, version or effective date. Only `source` = file name (`ingest.py:53`). |
| 6 | RAG workflow | Partial | Retrieve, prompt, generate, cite. No query classification, no confidence scores (honestly omitted), no escalation. |
| 7 | Adaptive interviews | Missing | — |
| 8 | Requirement structure | Partial | Has ID, statement, priority and global assumptions. Missing category, source, rationale, dependencies, acceptance criteria, regulation, risk, confidence and approval status. |
| 9 | Multi-label classification | Partial | Binary FR/NFR only. |
| 10 | Requirement quality analysis | Missing | The prompt asks for testable requirements; nothing checks it. |
| 11 | Compliance and security mapping | Partial | Requirement ID → KB file plus a risk level. No jurisdiction, gaps or approval checkpoints. |
| 12 | Artefacts (SRS, RTM, ...) | Missing | — |
| 13 | SDLC decision factors | Partial (strong) | 9 traits cover about 7 of the 13 factors. Missing: security risk, size, legacy dependence, continuous delivery, budget/schedule, documentation. |
| 14 | SDLC selection engine | Partial (strong) | Hybrid rules + LLM explanation with a ranked output ✓. No DevSecOps or hybrids, no approval step. |
| 15 | Project-specific workflow | Missing | — |
| 16 | Human-in-the-loop | Missing | Users can't accept, reject or edit any output. |
| 17 | Platform security | Missing | No auth, RBAC, masking, audit log or injection filter. Only `.env` hygiene and HTML escaping. |
| 18 | Prototype stack | Partial | LLM, vector DB, rule engine and web UI ✓. No relational DB, workflow engine, document generator, IAM or monitoring. |
| 19 | Evaluation metrics | Missing (near) | Unit tests only. No precision/recall, hallucination rate or expert comparison. |
| 20 | Deploy, monitor, audit | Missing | Prompts and decisions aren't logged. |

## C. Top 10 strengths

1. **The LLM never picks the model.** It only rates traits; `sdlc_rules.py` decides. This is the project's best design decision.
2. **Rubric-anchored traits at temperature 0** (`TRAIT_RUBRICS`, `sdlc_agent.py:217`). Live traits were identical across two runs for payroll, railway and food delivery. Earlier session logs show the same winner 3 of 3 times per scenario.
3. **Different scenarios get different recommendations**, and payroll volatility came out at 1 as intended.
4. **The "why not" text is rule-based** (`explain_mismatch`), so it can't contradict the scores. The live justifications never contradicted a score either.
5. **Careful tie and close-call handling**, with a test that all 120 profile orderings give the same ranking.
6. **Honest when the KB doesn't apply:** railway returned `not_relevant` with no citations.
7. **The deterministic engine resists prompt injection:** the "rate everything 5" injection had no effect.
8. **Clean modular code** with a single Gemini wrapper that handles 404/503/empty responses.
9. **44 offline tests run in 2.5 s**, so you can refactor safely.
10. **Honest documentation:** paraphrased KB, "no confidence percentages are shown, because none are computed", and `.env` was never in git history.

## D. Top 10 weaknesses

1. **The RAG relevance filter does nothing** (detailed in E1).
2. **LLM output is never validated** (`parse_json_response` returns whatever comes back).
3. **Shallow requirements:** inflated priorities, invented numbers presented as requirements, untestable NFRs, e.g. social media NFR-1 ("architecture shall be modular") and railway NFR-3 ("shall undergo rigorous V&V", a process, not a product property).
4. **Profiles and weights are asserted, not justified:** no literature reference and no sensitivity analysis.
5. **Trait evidence is never checked or shown.** The social-media "evidence" was often the rubric text copied back.
6. **Unstable when the scenario doesn't state its traits:** the banking winner flipped between runs. The close-call flag did fire, which helps.
7. **Not really agentic:** a linear chain with no feedback between agents. Compliance findings never reach the SDLC scoring.
8. **No evaluation:** no ground-truth set, accuracy or citation-correctness figures.
9. **Tiny KB without metadata.** Citations name whole files, and healthcare-privacy chunks show up as noise for social media and payroll.
10. **Large gap against the financial-sector problem statement:** no human review, no artefacts, no platform security.

## E. Critical issues

1. **RAG grounding is inconsistent.** In all four live runs `sources_used` was `[]`: every chunk scored 0.67–0.82, above the 0.65 cut-off. Yet three runs showed cited considerations (e.g. food delivery cites `payment_security` at distance 0.67).
   - **Cause:** the filtered list is only used for the `sources_used` field. All chunks still go into the prompt (`compliance_agent.py:87`).
   - **Why the distances are high:** the query is the domain plus every requirement joined together (`:75-78`), which dilutes it. My short probes scored 0.45–0.58 on good matches.
   - **Viva risk:** "Your system says it used no sources but shows three citations. Explain."
2. **"Never fabricates citations" is only a prompt instruction.** In an offline probe, a mocked LLM reply citing "GDPR Article 17", with risk level "Extreme" and an unknown requirement FR-99, came back as 200 OK, ready for the UI to display.
3. **A silent fallback tilts the result.** A missing or unparseable trait becomes 3 (`sdlc_agent.py:223-227`). An all-3 project scores Incremental 83.2 against Waterfall 57.5, so a parsing failure quietly recommends Incremental.
4. **"No model has a structural advantage" is overstated** (`sdlc_rules.py:145`, ARCHITECTURE). Over all 5⁹ trait vectors, Incremental wins 39.7% and Waterfall 8.8%; Incremental's lowest possible score is 33.2, Waterfall's is 7.5. Central profiles are closer to everything. The fairness test (each model scores 100 on its own profile) can't catch this.
5. **Evidence is not grounded.** Banking run 1 cited "RBI guidelines" that aren't in the scenario (probably introduced by the generated requirements), and social-media evidence repeated rubric anchors word for word. The UI never shows evidence, so nobody can audit it.
6. **Smaller correctness issues:**
   - `meta.model` always reports `GEMINI_MODEL`, even after a fallback model answered.
   - A 429 rate-limit skips straight to the next model with no backoff (`gemini_client.py:62`).
   - `compliance_agent.py:22` says "cosine distance"; the metric is L2.
   - The `main.py` docstring says port 8000, and the FastAPI title is "SE Project".
   - Railway NFR-1 treats SIL 4 as an availability rating. SIL measures how unlikely a dangerous failure is, not availability, and an examiner may notice.

## F. Viva preparation

1. **"Why do you call these agents?"** Admit they are LLM-backed components in a fixed pipeline. Explain why you chose deterministic orchestration (reproducible, testable), and what would make them agentic: tool use, feedback loops, a coordinator.
2. **"Prove the LLM doesn't choose the model."** Point to `score_models`, which is pure Python. The LLM only produces 9 integers, and the prompt never mentions model profiles.
3. **"Where do the profile numbers come from?"** Cite Boehm & Turner's "home grounds", Sommerville and Pressman. Admit they are expert judgement, and show a sensitivity analysis.
4. **"Why weighted L1 distance and not cosine or AHP?"** L1 is interpretable per trait and fits the "why not" explanations. AHP would need pairwise judgements. Know the limits, including the centrality effect.
5. **"Is the scoring fair? Can Incremental win more often?"** Explain the 39.7% win share, why central profiles benefit, and that the self-profile test is necessary but not sufficient.
6. **"What's the difference between criticality and risk/uncertainty?"** Stakes vs novelty: railway is 5 and 1.
7. **"What happens if the LLM rates a trait 1 point wrong?"** Show how scores shift (each step costs weight/38.8 × 100 points), the close-call threshold, and the banking flip.
8. **"How reproducible is the output?"** Temperature 0 for traits, 0.1 for requirements. Show identical traits on repeat runs, and say what still varies.
9. **"Explain your RAG pipeline end to end."** Paragraph chunking, Gemini embeddings, Chroma with squared L2, top-5 retrieval, threshold, prompt. Know that squared L2 on unit vectors equals 2 − 2·cos, so 0.65 means cosine above about 0.675.
10. **"Why did sources_used come back empty?"** Long-query dilution and an untuned threshold. Describe the fix (E1).
11. **"How do you stop invented regulations?"** Today it's a prompt instruction. A real fix is deterministic validation of `source` against the retrieved set.
12. **"How was 0.65 chosen?"** Be honest: tuned by hand. Better: a labelled set of query/chunk pairs and a precision-recall curve.
13. **"Who decides the risk level?"** The LLM, and it isn't grounded (railway is "High" with no KB support). Propose rules based on data sensitivity and regulated activity.
14. **"Is FR-1 testable? Write an acceptance test."** Practise Given/When/Then for one live requirement.
15. **"Why are almost all priorities High?"** No distribution guidance. Propose MoSCoW with limits.
16. **"Is '100% computational accuracy without rounding' a good requirement?"** No: payroll needs defined rounding rules. Say what makes a requirement verifiable.
17. **"Why no DevSecOps or hybrid models when the statement lists them?"** DevSecOps is a delivery and security practice layered on an iterative model, not a peer of Waterfall. Hybrids could be scored as profile blends.
18. **"How do you handle bad LLM output?"** Fence stripping, a 502 on invalid JSON, model fallback. Admit there's no schema validation yet.
19. **"How would you evaluate this against human experts?"** A labelled scenario set, expert SDLC labels, agreement metrics (accuracy, Cohen's κ), stability over N runs, citation precision.
20. **"Security: what if someone pastes customer data, or a prompt injection?"** Data goes to Gemini unmasked. Injection is contained because the decision is deterministic (show the probe), but no masking exists.

## G. Improvement roadmap

**Critical (must fix before submission)**

| Fix | Benefit | Where |
|---|---|---|
| Re-tune retrieval: use short or per-requirement queries, send only chunks under the threshold, set `kb_relevance` from distances, drop any consideration whose `source` wasn't retrieved | Makes the RAG claim true; worth about +3–4 marks | `compliance_agent.py`, `retriever.py` |
| Pydantic validation for all four LLM outputs (requirements, compliance, traits, justification): allowed enums, known requirement IDs, retry once and then fail cleanly | Stops invented citations and broken data | the agents, `main.py` |
| A missing trait raises an error instead of becoming 3 | Removes the hidden Incremental tilt | `sdlc_agent.py:223` |
| Show trait evidence and reasons, plus retrieved chunks with distances, by extending the existing panels | Explainability you can actually see | `app.js` |
| Correct the overstated claims and stale comments | Avoids viva traps | README, ARCHITECTURE, `sdlc_rules.py:145`, `compliance_agent.py:22`, `main.py` |

**Important (would raise the marks a lot)**
- **Evaluation script:** 12–20 labelled scenarios, including some that don't state their traits, with 3 runs each. Report accuracy, stability, close-call rate and citation precision in `docs/EVALUATION.md`. This is the biggest single gain for the testing marks.
- **Justify the profiles and weights:** add references, a sensitivity table (perturb each weight and profile cell, check whether the winner changes) and the win-share analysis. Files: `sdlc_rules.py`, ARCHITECTURE.
- **Check evidence against the input:** fuzzy-match each quote against the scenario and requirements, and mark misses as "inferred". File: `sdlc_agent.py`.
- **Requirement agent:** add optional fields (category tags, acceptance criteria), require invented numbers to appear in `assumptions`, and add priority guidance. Adding optional fields keeps the response shape stable. File: `requirement_agent.py`.
- **Pass compliance risk into the trait prompt** as evidence for criticality and V&V, not as a bonus. This is listed as an idea in CLAUDE.md.
- **Gemini client:** back off on 429 and report the model that actually answered. Files: `gemini_client.py`, `main.py`.
- **Input limits:** cap scenario length and remove or guard `/test-gemini`. File: `main.py`.

**Optional**
- Requirement editing before stage 2 (basic human review).
- Markdown SRS export.
- JSONL audit log.
- Upgrade `google-genai` and set the embedding `task_type`.
- KB metadata (jurisdiction, version).
- Only add DevSecOps or hybrid profiles if you can defend them.

## H. Professor's remarks

> *Requora AI shows a clear understanding of why LLM output shouldn't be trusted to make engineering decisions. Splitting rubric-based trait extraction from a deterministic, tested scoring engine is the most mature idea in the submission, and it holds up in live runs: stable, distinct recommendations that match textbook expectations for agile, regulated, safety-critical and modular projects. Code quality and documentation are above average for this level.*
>
> *However, several claims go beyond the implementation. The RAG relevance filter discarded every retrieved chunk in live runs while the generator still cited them. "No fabricated citations" relies on a prompt, not a check. The scoring fairness claim ignores a measurable tilt toward Incremental. Requirements are tidy but shallow, with inflated priorities and invented numbers. There is no evaluation beyond unit tests. Against the full financial-sector problem statement, most of the system (human review, artefacts, security, traceability) is still to be built. Fix grounding and validation first, then add a small evaluation study. Those two changes would move this from a good demo to a defensible engineering project.*
>
> **Eval-1: 62/100. Full-scope readiness: about 25/100.**
