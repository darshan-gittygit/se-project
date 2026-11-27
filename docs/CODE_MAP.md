# Requora AI - code map (for the demo and viva)

One request to `POST /agents/pipeline` turns a scenario into requirements, compliance and risk findings, an SDLC recommendation, a workflow and SRS artefacts. **8 calls go to an LLM; everything else is plain Python.** The LLM never picks the SDLC model, never sets a score, an id, a confidence or an approval status: code does.

Names below are real file, function and constant names; open the file to show them.

---

## 1. Every file under `app/`

| File | What it does | Kind |
|---|---|---|
| `main.py` | FastAPI app: all routes, error handlers, the pipeline (`run_full_pipeline`), per-stage audit events, SRS download | code |
| `config.py` | Reads `.env` (API key, `GEMINI_MODEL`, DB path, `REQUIRE_ROLE`, `RETENTION_DAYS`), project paths, `APP_VERSION` | code |
| `gemini_client.py` | `generate_text()`: the only place that calls the Gemini text API; model fallback, 503/429 retries, call and model tracking | code (wraps the LLM) |
| `schemas.py` | Pydantic schemas for LLM replies, `generate_validated()` (validate + retry once), requirement quote check and confidence | code |
| `json_utils.py` | `parse_json_response()`: strips markdown fences before `json.loads` | code |
| `vocabulary.py` | Word-for-word lists from the problem statement: stakeholder roles, interview topics, requirement categories, levels | data |
| `agents/requirement_agent.py` | Agent 1: scenario (+ answers, documents) -> requirement records | LLM + code checks |
| `agents/interview_agent.py` | Next follow-up interview questions (`/agents/interview`, not in the pipeline) | LLM + code |
| `agents/interview_templates.py` | 27 hand-written role-specific interview questions | data |
| `agents/quality_agent.py` | Ambiguity, untestable and duplicate rules; conflicts by one LLM call | code + LLM |
| `agents/compliance_agent.py` | Agent 3: retrieval, relevance, compliance analysis, source filtering | code + LLM |
| `agents/compliance_mapping.py` | Per-requirement mapping: confidence grade, evidence check, review flags | code |
| `agents/risk_agent.py` | Threat and risk register: LLM rates likelihood/impact, code scores and ranks | LLM + code |
| `agents/sdlc_agent.py` | Agent 2: LLM rates 15 traits, code scores, LLM explains | LLM + code |
| `agents/sdlc_rules.py` | The deterministic SDLC scoring engine (weights, profiles, tie-break, close call) | code |
| `agents/sdlc_workflows.py` | Base phases of each of the 7 SDLC models (section 15 fields) | data |
| `agents/workflow_agent.py` | Adds project-specific items to the base workflow (cannot remove any) | LLM + code |
| `agents/artefact_agent.py` | User stories, use cases, process workflows, data and interface requirements | LLM + code |
| `artefacts.py` | Traceability matrix, compliance-control matrix, assumptions/dependency register, open issues | code |
| `srs.py` | Builds the SRS document model and renders Markdown or printable HTML | code |
| `rag/embeddings.py` | `embed_texts()`: Gemini embedding calls (batches of 100) | code (wraps the embedding model) |
| `rag/ingest.py` | `python -m app.rag.ingest`: chunks `knowledge_base/*.txt`, embeds, stores in ChromaDB | code |
| `rag/retriever.py` | `retrieve_many()`: embeds queries, top-k ChromaDB search | code |
| `rag/kb_metadata.py` | `knowledge_base/sources.json`, domain tags, which sources apply, KB version hash | code |
| `masking.py` | Finds emails, cards, IBANs, Aadhaar/PAN, phones...; replaces them with `[EMAIL_1]`-style placeholders | code |
| `prompt_safety.py` | Data delimiters for prompts, injection-pattern filter, output secret/PII check | code |
| `input_guard.py` | `Guard`: one per request; runs the filter and masking on every user field; document upload rules | code |
| `roles.py` | Roles from the `X-Role` header and the permission table | code |
| `approvals.py` | Accept / reject / modify / regenerate, versions, baseline freeze | code (regenerate re-runs an LLM stage) |
| `runs.py` | Stores runs and approval items; applies current approval state to a stored result | code |
| `db.py` | SQLite schema and connections; triggers make decisions and audit events append-only | code |
| `audit.py` | Hash-chained audit log: append, verify, query | code |
| `retention.py` | `python -m app.retention`: deletes old runs (audit kept) | code |
| `static/index.html`, `style.css`, `app.js`, `charts.js` | The web UI (vanilla JS); `charts.js` draws the SVG charts | UI code |
| `static/sample_run.json` | A recorded live pipeline result for "Load recorded sample" | data |

---

## 2. The LLM calls in `/agents/pipeline`, in run order

All calls go through `schemas.generate_validated()` -> `gemini_client.generate_text()`. `generate_validated` parses the JSON (`parse_json_response`), validates it against the schema with request facts (known requirement ids, quotable texts, real KB sources), runs `prompt_safety.output_problem()` (keys or unmasked personal data in the reply), and on any failure retries **once** with the reason appended (`RETRY_NOTE`). A second failure raises `LLMOutputError`, which the API returns as **502**. Rejected answers' reasons go into the stage's audit event (`validation_retries`).

| # | Stage (audit name) | File -> function | Prompt template | Output schema | Temp. | What code does with the reply |
|---|---|---|---|---|---|---|
| 1 | `requirements` | `requirement_agent.extract_requirements` | `PROMPT_TEMPLATE` | `RequirementsOutput` (`schemas.py`) | 0.1 | Each `source_text` must be a verbatim quote from the scenario, an answer or a document, else retry (`complete_requirement`); code sets ids, type, `source_document`, `confidence` (`_confidence`) and `approval_status` |
| 2 | `compliance` | `compliance_agent.analyze_compliance` | `PROMPT_TEMPLATE` | `ComplianceOutput` (`schemas.py`) | 0.2 | Before: retrieval, threshold, domain filter, `kb_relevance`. After: `filter_considerations` drops sources not retrieved; `build_mapping` grades confidence and sets review flags; `main.py` calls `attach_regulations` |
| 3 | `quality` | `quality_agent.conflict_flags` (inside `analyze_quality`) | `CONFLICT_PROMPT` | `ConflictOutput` | 0.1 | Joined with the rule-based ambiguity, untestable and duplicate flags; counts per type |
| 4 | `risk_register` | `risk_agent.build_risk_register` | `PROMPT_TEMPLATE` (+ `LIKELIHOOD_RUBRIC`, `IMPACT_RUBRIC`) | `RiskOutput` (5-8 risks) | 0.2 | score = likelihood x impact, `level_for` (High >= 10, Medium 5-9, Low 1-4), rank, `RISK-n` ids, summary |
| 5 | `sdlc` (traits) | `sdlc_agent.analyze_traits` | `TRAIT_PROMPT_TEMPLATE` (+ `TRAIT_RUBRICS`) | `TraitRatings` (all 15, integers 1-5) | 0 | Scores go to `sdlc_rules.score_models`; **code** ranks the 7 models and picks the top one |
| 6 | `sdlc` (explanation) | `sdlc_agent.recommend_sdlc` | `JUSTIFICATION_PROMPT_TEMPLATE` | `Justification` | 0.3 | Must mention only real requirement ids; trait keys turned into plain names (`_humanize_trait_keys`). The "why not" text per model is written by code (`explain_mismatch`) |
| 7 | `workflow` | `workflow_agent.generate_workflow` | `PROMPT_TEMPLATE` | `WorkflowAdditions` | 0.2 | Additions merged into `BASE_WORKFLOWS[model]` with `source: "scenario"`; base phases and items cannot be removed or renamed |
| 8 | `artefacts` | `artefact_agent.generate_artefacts` -> `_call` | `PROMPT_TEMPLATE` + `SECTION_RULES` | `AllArtefacts` (split into `PartStories` + `PartModels` above `SPLIT_THRESHOLD` = 20 requirements: 2 calls) | 0.2 | `_finish` assigns `US-1`... ids, builds the story sentence, links source statements; then `artefacts.build_code_artefacts` adds the RTM and registers (no LLM) |

`?artefacts=false` skips call 8. Stages 5 and 6 are one audit stage (`sdlc`).

**Which model answers.** `generate_text` tries `GEMINI_MODEL` from `.env` (default in `config.py`: `gemini-3.5-flash-lite`), then `FALLBACK_MODELS` = `gemini-3.5-flash-lite`, `gemini-3.8-flash`, `gemini-flash-latest` (duplicates skipped). Per model: a 503 is retried with 2, 4, 8 s waits (at most 15 s in total); a 429 up to 3 times using the API's wait hint (at most 10 s); a hint over 60 s (a daily quota) moves straight to the next model. The model that answered each call is recorded (`track_models`) and returned in `meta` / `versions`. In the live runs of 2026-11-26 every call was answered by `gemini-3.5-flash-lite`.

**LLM calls outside the pipeline:** `/agents/interview` (`interview_agent.next_questions`, `FollowUpOutput`, 0.2); approval "regenerate" (`approvals._regenerate`): a requirement via `requirement_agent.regenerate_requirement` (`REGENERATE_TEMPLATE`, `RegeneratedRequirement`, 0.2), compliance or SDLC traits by re-running stage 2 or 5 with the reviewer's comment (`REVIEWER_NOTE`).

---

## 3. The embedding call (RAG)

| | |
|---|---|
| File, function | `rag/embeddings.py` -> `embed_texts()` (batches of up to 100 texts) |
| Model | `EMBEDDING_MODEL` = `gemini-embedding-001` |
| Embedded at build time | `rag/ingest.py` -> `build_knowledge_base()`: each `knowledge_base/*.txt` split on blank lines (`_chunk_text`, one chunk per paragraph), stored in ChromaDB collection `compliance_kb` in `chroma_db/` with metadata `source` = file name |
| Embedded per request | `compliance_agent.build_queries`: one query per requirement, the requirement text alone; `retriever.retrieve_many` embeds them in one batch and takes the top `TOP_K_PER_QUERY` = 3 chunks each |
| Threshold | `RELEVANCE_THRESHOLD` = **0.62** (`compliance_agent.py`), on ChromaDB's default squared-L2 distance; lower is closer. Tuned by `scripts/tune_relevance_threshold.py` (`docs/RAG_TUNING.md`). At most `MAX_CONTEXT_CHUNKS` = 8 chunks reach the prompt |

The knowledge base is paraphrased principles written for this project, not law text.

---

## 4. The deterministic parts

**SDLC scoring** - `agents/sdlc_rules.py`
- 15 traits, each rated 1-5 by the LLM against fixed rubrics (`TRAIT_RUBRICS` in `sdlc_agent.py`).
- `TRAIT_WEIGHTS`: risk/uncertainty 1.3; volatility, stakeholder feedback, continuous delivery 1.2; verification & validation, prototyping 1.1; clarity, criticality, incremental suitability, security risk 1.0; complexity, legacy dependence, stakeholder availability 0.8; system size, budget/schedule 0.6.
- `SDLC_PROFILES`: one fixed 1-5 profile per model (Waterfall, V-Model, Incremental, Agile, Spiral, DevSecOps, Agile-V).
- `_rank` / `score_models`: score = (1 - weighted absolute distance / maximum possible distance) x 100. Same formula for every model, no per-model bonus; a model scores 100 on its own profile. `tests/test_scoring.py` enforces fairness.
- Tie (`TIE_TOLERANCE` = 0.05 points, `top_two_tied`): smaller worst single-trait gap wins, then smaller total gap, then alphabetical; `explain_tie_break` writes the sentence.
- Close call (`CLOSE_CALL_MARGIN` = 5.0 points, `is_close_call`): the UI shows a banner and the explanation must say it is not decisive.
- `explain_mismatch`: the "why not" sentence (largest trait gap) for every non-top model.

**Compliance relevance and confidence** - `agents/compliance_agent.py`, `agents/compliance_mapping.py`, `rag/kb_metadata.py`
- `classify_domains` + `applies`: a source counts only if `sources.json` says it applies to the scenario's domain tags.
- `kb_relevance_from`: "relevant" if at least half the requirements (`RELEVANT_COVERAGE` = 0.5) match a domain-specific chunk under 0.62; "partially_relevant" if some do; else "not_relevant". The generic security baseline never counts as coverage.
- `filter_considerations`: drops citations of sources that were not retrieved for this request.
- `build_mapping`: one item per (requirement, source); if several findings cite the same source, the most specific one wins.
- `evidence_verified`: the quote has at least 5 words and appears (case/punctuation ignored) in a retrieved chunk of that source.
- `grade_confidence`: High = distance < 0.50 and verified; Medium = < 0.60 and verified; else Low.
- `_review_reasons`: human review for Low confidence, not assessed, not applicable, cited without a match, a gap on a High-risk requirement, or a Regulatory requirement with no domain-specific support.

**Requirement checks** - `schemas.py`: `complete_requirement` (quote must be verbatim, at least 2 words, or assumptions listed), `_confidence` (High: quote, no assumptions; Medium: quote + assumptions; Low: no quote).

**Quality rules** - `agents/quality_agent.py`: `ambiguity_flags` (`VAGUE_TERMS`), `untestable_flags` (performance/availability NFR without a number), `duplicate_flags` (difflib similarity >= `DUPLICATE_SIMILARITY` = 0.92).

**Risk score** - `agents/risk_agent.py`: `level_for` with `LEVEL_BANDS` (High >= 10, Medium >= 5, Low >= 1); sort by score, then impact, then likelihood.

**Masking** - `masking.py`: `Masker.mask` / `mask_obj` / `report`, `detect`. Order: email, IBAN (mod-97), labelled account, card (Luhn), SSN, Aadhaar-style, PAN-style, phone. Same value -> same placeholder within a request; placeholders keyed by SHA-256, originals never stored. Applied by `input_guard.Guard.text` before any model call, and again on outputs (`Guard.output`) before storage.

**Injection filter** - `prompt_safety.py`: `untrusted()` wraps user text in `<<<BEGIN ... (data, not instructions)>>>` blocks with `DATA_RULE` in every prompt; `injection_pattern()` matches `SQUASHED_PATTERNS` (after `_squash`: lower case, no spaces/punctuation/zero-width, simple leetspeak) and `RAW_PATTERNS`. A match -> `InjectionBlocked` -> 400 + `injection_blocked` audit event (field and pattern, never the text). `output_problem()` rejects replies containing key-like text or unmasked personal data.

**Roles and approvals** - `roles.py`: `parse_role`, `require`, `review_permission`, `PERMISSIONS`. `runs.py`: `save_run`, `initial_items` (each requirement and each domain-specific compliance mapping is mandatory for the baseline; the SDLC item is not), `with_current_state`. `approvals.py`: `act` (accept/reject/modify/regenerate; comment required except accept; `MAX_REGENERATIONS` = 3; new versions via `_new_version`; any change reopens a frozen baseline via `_reopen_baseline`), `blockers`, `freeze_baseline` (409 listing blockers unless all mandatory items are approved). SDLC is approved only when all four roles in `SDLC_APPROVERS_REQUIRED` accept the same version.

**Audit chain** - `audit.py`: `append_event` (inside `BEGIN IMMEDIATE`; `row_hash` = SHA-256 of the previous row's hash + this row's canonical JSON), `verify_chain` (recomputes every hash, names the first bad row), `query_events`. `db.py` triggers abort any UPDATE or DELETE on `audit_events` and `decisions`.

**Artefacts and SRS by code** - `artefacts.py`: `build_code_artefacts` -> `traceability_matrix`, `compliance_control_matrix`, `assumptions_register`, `open_issues`. `srs.py`: `build_blocks`, `render_markdown`, `render_html`.

---

## 5. One request, browser to response ("Run full analysis")

1. `static/app.js` -> `runPipeline()` -> `api("POST", "/agents/pipeline", body)`: `fetch` with the `X-Role` header from the role dropdown; body = scenario, answers, documents.
2. FastAPI validates the body (`ScenarioRequest`) and resolves `role_header` -> `roles.parse_role` (400 for an unknown role).
3. `main.run_full_pipeline`: `roles.require("run_pipeline", ...)` (auditor -> 403), `_check_scenario`, `runs.new_run_id`.
4. `input_guard.Guard(...)`; `guard.text("scenario")`, `guard.answers`, `guard.documents` -> `prompt_safety.injection_pattern` (400 on a match) then `Masker.mask`.
5. `audit.append_event("pipeline_started")`; `track_models`, `track_calls`, `track_retries` start recording.
6. For each stage the local `stage()` helper runs the agent and then `_audit_stage` -> `append_event("stage_run")` (prompt hashes, model, retry reasons, masked output). A failure writes `pipeline_failed` and returns 502.
   - `extract_requirements` -> `analyze_compliance` (`retrieve_many` -> `embed_texts`; LLM; `build_mapping`) -> `attach_regulations` -> `analyze_quality` -> `build_risk_register` -> `recommend_sdlc` (`analyze_traits` -> `score_models` -> justification) -> `generate_workflow` -> `generate_artefacts`.
   - Each LLM step: prompt `.format(...)` with `untrusted()` blocks -> `generate_validated` -> `generate_text` -> `_client.models.generate_content`.
7. `artefacts.build_code_artefacts`; the result dict is assembled; `guard.output(result)` masks it again; `masking` and `versions` are added; one `artefact_generated` event per artefact.
8. `runs.save_run` stores the result and registers approval items; `append_event("pipeline_completed")`; `run_id` and SRS links are added; FastAPI returns JSON.
9. Back in `app.js`: `maskingBanner`, `history.replaceState` to `#run=<id>`, `loadRun()` -> `GET /runs/<id>` (approval items), then `render()` draws every tab (`charts.js` for the radar, heat matrix, bars, risk grid).

---

## 6. Fifteen likely viva questions

1. **Does the LLM choose the SDLC model?** No. It only rates 15 traits on fixed rubrics with evidence; `sdlc_rules.score_models` computes a weighted distance to each model's fixed profile and the closest model wins (`agents/sdlc_rules.py`).
2. **How do you know the scoring is fair to every model?** All seven use the same formula with no per-model bonus, and each scores exactly 100 on its own profile. `tests/test_scoring.py` checks this; `docs/SCORING_ANALYSIS.md` reports win shares and sensitivity.
3. **What if two models score almost the same?** Within 0.05 points it is a tie, broken by the smaller worst trait gap, then total gap, then name (`_rank`). Within 5 points it is a close call, shown in the UI and stated in the explanation (`is_close_call`).
4. **How do you stop hallucinated requirements?** Every quote must appear word for word in the scenario, answers or documents, or the record must list its assumptions; otherwise the reply is rejected and retried (`schemas.complete_requirement`). Confidence is computed from that, not reported by the model (`_confidence`).
5. **How do you stop invented regulations?** The prompt shows only retrieved chunks and lists allowed source names; schema validation rejects unknown sources and `filter_considerations` drops sources not retrieved for this request (`agents/compliance_agent.py`). Evidence quotes are checked against the chunk text (`evidence_verified`).
6. **What does RAG mean here, concretely?** Each requirement is embedded with `gemini-embedding-001`, ChromaDB returns the 3 closest knowledge-base paragraphs, and only those under distance 0.62 that apply to the domain go into the prompt (`rag/retriever.py`, `compliance_agent.py`). The knowledge base is paraphrased principles, not law.
7. **Why 0.62?** It gave the best F0.5 (0.909: precision 0.94, recall 0.81) on hand-labelled requirement-source pairs (37 relevant, 85 irrelevant) with one query per requirement (`scripts/tune_relevance_threshold.py`, results in `docs/RAG_TUNING.md`). F0.5 favours precision because a wrong citation is worse than a missed one; it must be re-tuned if the metric, embedding model or query format changes.
8. **What happens when the model returns bad JSON?** `generate_validated` retries once with the validation errors appended, then raises `LLMOutputError`, which the API returns as 502 instead of guessing (`schemas.py`). The rejection reason is stored in the audit event.
9. **What if Gemini is overloaded or rate-limited?** 503s are retried with 2/4/8 s waits, 429s with the API's wait hint, and a long daily-quota hint moves to the next fallback model (`gemini_client.generate_text`). Every attempt's reason is listed if all models fail.
10. **How is sensitive data protected?** `masking.Masker` replaces emails, card numbers, IBANs, Aadhaar/PAN-style ids and phones with placeholders before any model call or storage, and outputs are masked again (`input_guard.Guard`). Names and addresses are not detected - a known limit.
11. **How do you handle prompt injection?** User text is fenced as data in every prompt (`untrusted`), obvious injection phrases are rejected with 400 (`injection_pattern`), and replies with key-like text or unmasked data are rejected (`output_problem`), all in `prompt_safety.py`. A pattern list cannot catch everything; schema validation and code-made decisions are the main defence.
12. **Who can approve what?** The `X-Role` header selects a role and `roles.PERMISSIONS` decides: analysts/product owners review requirements, the security officer high-risk security requirements, the compliance officer interpretations, and all four of PM, architect, security and compliance must accept the SDLC choice (`roles.py`, `approvals.act`). The header is a stand-in, not authentication.
13. **How is the audit log tamper-evident?** Each row stores the previous row's hash and a SHA-256 over its own content, so `verify_chain` finds the first edited, removed or reordered row (`audit.py`). Database triggers also block UPDATE and DELETE (`db.py`).
14. **How is the risk register scored?** The LLM rates likelihood and impact 1-5 on written anchors with a reason each; code multiplies them, bands the score (High >= 10) and ranks (`agents/risk_agent.py`). It asks for 5-8 risks linked to real requirement ids.
15. **How do you evaluate the system?** Six case studies with frozen, hand-written gold; `scripts/run_eval.py` runs them and `scripts/evaluate.py` computes precision/recall/F1, ambiguity and conflict detection, regulatory coverage, hallucination rate, citation correctness, SDLC top-1/top-3, correction rate and traceability (`docs/EVALUATION.md`). Limits: small sample, one labeller, keyword matching.

---

**Careful in the viva:** the docstring of `run_full_pipeline` still says the UI calls three separate endpoints; since the UI rebuild it calls `/agents/pipeline` once. The win-share figures in the `score_models` docstring (5^9 vectors, 39.7%) date from the 9-trait version; quote `docs/SCORING_ANALYSIS.md` instead.
