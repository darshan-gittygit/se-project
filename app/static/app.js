/* Requora AI front end. Vanilla JS, no framework, no build step.
   Renders only fields the API returns (see app/main.py and docs/COVERAGE.md).
   Every request carries the X-Role header chosen in the top bar. */
(function () {
  "use strict";

  // ---------------------------------------------------------------- constants
  var TRAIT_KEYS = ["clarity", "volatility", "complexity", "criticality", "risk_uncertainty", "verification_validation",
    "stakeholder_feedback", "incremental_suitability", "prototyping_need", "security_risk", "system_size",
    "legacy_dependence", "continuous_delivery", "stakeholder_availability", "budget_schedule_constraint"];
  // Short display names for TRAIT_LABELS in app/agents/sdlc_rules.py (the API returns keys only).
  var TRAIT_LABELS = {
    clarity: "Requirement clarity", volatility: "Requirement volatility", complexity: "Project complexity",
    criticality: "Project criticality", risk_uncertainty: "Risk / uncertainty",
    verification_validation: "Verification & validation", stakeholder_feedback: "Stakeholder feedback need",
    incremental_suitability: "Incremental delivery fit", prototyping_need: "Prototyping need",
    security_risk: "Security risk", system_size: "System size", legacy_dependence: "Legacy dependence",
    continuous_delivery: "Continuous delivery need", stakeholder_availability: "Stakeholder availability",
    budget_schedule_constraint: "Budget / schedule constraint"
  };
  var SDLC_MODELS = ["Waterfall", "V-Model", "Incremental", "Spiral", "Agile", "DevSecOps", "Agile-V"];
  var SDLC_APPROVERS = ["project manager", "architect", "security officer", "compliance officer"];
  var AUDIT_ROLES = ["auditor", "compliance officer"];
  var ROLES = ["business analyst", "product owner", "security officer", "compliance officer", "project manager", "architect", "auditor"];
  var LEVELS = ["High", "Medium", "Low"];
  var DOC_EXT = [".txt", ".md", ".csv"], MAX_DOCS = 5, MAX_DOC_CHARS = 20000, MAX_SCENARIO = 5000;
  var STAGES = [
    { id: "scenario", name: "Scenario" },
    { id: "requirements", name: "Requirements" },
    { id: "compliance", name: "Compliance and risk" },
    { id: "sdlc", name: "SDLC" },
    { id: "artefacts", name: "Artefacts" },
    { id: "approvals", name: "Approvals" }
  ];
  var ART_TABS = [
    { id: "stories", name: "User stories" }, { id: "usecases", name: "Use cases" }, { id: "workflows", name: "Workflows" },
    { id: "data", name: "Data and interfaces" }, { id: "rtm", name: "Traceability matrix" },
    { id: "controls", name: "Compliance controls" }, { id: "assumptions", name: "Assumptions and dependencies" },
    { id: "issues", name: "Open issues" }
  ];

  // ---------------------------------------------------------------- state
  var S = {
    role: localStorage.getItem("requora.role") || "business analyst",
    tab: "scenario", artTab: "stories",
    input: { scenario: "", answers: [], documents: [], artefacts: true },
    templates: null, followUps: null, interviewBusy: false,
    result: null, run: null, sample: false,
    running: false, startedAt: 0, lastElapsed: null, timer: null,
    audit: null, auditFilters: { run_id: "", role: "", action: "", limit: 200 },
    banners: [], busy: false
  };
  if (ROLES.indexOf(S.role) < 0) S.role = "business analyst";

  // ---------------------------------------------------------------- helpers
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function $(id) { return document.getElementById(id); }
  function list(arr, empty) {
    if (!arr || !arr.length) return empty == null ? '<span class="muted">-</span>' : empty;
    return "<ul>" + arr.map(function (x) { return "<li>" + esc(typeof x === "string" ? x : x.text) + "</li>"; }).join("") + "</ul>";
  }
  function chips(arr, cls) {
    if (!arr || !arr.length) return '<span class="muted">-</span>';
    return arr.map(function (c) { return '<span class="chip ' + (cls || "") + '">' + esc(c) + "</span>"; }).join("");
  }
  function level(v) {
    if (!v) return '<span class="muted">-</span>';
    return '<span class="badge ' + esc(String(v).toLowerCase()) + '">' + esc(v) + "</span>";
  }
  function statusBadge(s) {
    var cls = { approved: "ok", satisfied: "ok", rejected: "bad", gap: "bad", modified: "warn", proposed: "", superseded: "",
                frozen: "ok", open: "warn", pending: "warn", not_applicable: "" }[s];
    return '<span class="badge ' + (cls || "") + '">' + esc(String(s || "-").replace(/_/g, " ")) + "</span>";
  }
  function fmtTime(ms) {
    var s = Math.floor(ms / 1000);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }
  function allReqs() {
    var r = S.result && S.result.requirements;
    return r ? (r.functional_requirements || []).concat(r.non_functional_requirements || []) : [];
  }
  function reqById(id) { return allReqs().filter(function (r) { return r.id === id; })[0]; }
  function item(type, id) {
    if (!S.run) return null;
    return S.run.items.filter(function (it) { return it.item_type === type && it.item_id === id; })[0] || null;
  }
  function canReviewReq(r) {
    var sec = r.risk_level === "High" && (r.categories || []).indexOf("Security") >= 0;
    return sec ? ["security officer"] : ["business analyst", "product owner"];
  }
  function live() { return S.result && !S.sample && S.run; }

  // ---------------------------------------------------------------- API
  function ApiError(status, message, data) { this.status = status; this.message = message; this.data = data; }
  var STATUS_TEXT = {
    0: "Could not reach the server", 400: "The request was rejected", 403: "Not allowed for this role",
    404: "Not found", 409: "Conflict", 422: "The request was not in the expected format",
    500: "Server error", 502: "The AI model step failed", 503: "The model service is unavailable"
  };
  function detailText(d) {
    if (d == null) return "";
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map(function (e) { return (e.loc ? e.loc.join(".") + ": " : "") + (e.msg || JSON.stringify(e)); }).join("; ");
    if (d.message) return d.message;
    return JSON.stringify(d);
  }
  function api(method, path, body) {
    var opts = { method: method, headers: { "X-Role": S.role } };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    return fetch(path, opts).catch(function (e) {
      throw new ApiError(0, "Could not reach the server (" + e.message + "). Is uvicorn running?");
    }).then(function (res) {
      var ct = res.headers.get("content-type") || "";
      var p = ct.indexOf("json") >= 0 ? res.json().catch(function () { return null; }) : res.text();
      return p.then(function (data) {
        if (!res.ok) {
          var detail = data && typeof data === "object" && "detail" in data ? data.detail : data;
          var msg = detailText(detail) || res.statusText;
          if (data && data.field) msg += " (field: " + data.field + ")";
          throw new ApiError(res.status, msg, detail);
        }
        return data;
      });
    });
  }
  function showError(e, context) {
    var status = e && e.status != null ? e.status : "";
    var head = (STATUS_TEXT[status] || "Error") + (status ? " (" + status + ")" : "");
    var body = esc(e && e.message ? e.message : String(e));
    if (e && e.data && e.data.blockers) {
      body += "<ul>" + e.data.blockers.map(function (b) {
        return "<li>" + esc(b.item_type + " " + b.item_id + " v" + b.version + " is " + b.status) + "</li>";
      }).join("") + "</ul>";
    }
    addBanner("error", "<strong>" + esc(head) + (context ? " - " + esc(context) : "") + ":</strong> " + body);
  }
  function addBanner(kind, html, key) {
    if (key) S.banners = S.banners.filter(function (b) { return b.key !== key; });
    S.banners.push({ kind: kind, html: html, key: key || null, id: Math.random().toString(36).slice(2) });
    var transient = S.banners.filter(function (b) { return b.key !== "sample" && b.key !== "masking"; });
    if (transient.length > 3) S.banners.splice(S.banners.indexOf(transient[0]), 1);   // keep the latest few messages
    renderBanners();
  }
  function clearBanners(kind) {
    S.banners = S.banners.filter(function (b) { return kind ? b.kind !== kind : b.key === "sample"; });
    renderBanners();
  }

  // ---------------------------------------------------------------- rendering: shell
  function render() {
    renderSidebar();
    renderTabs();
    renderBanners();
    renderPanel();
  }

  function stageInfo(id) {
    var r = S.result;
    if (id === "scenario") {
      if (S.running) return { st: "running", sum: "Running... " + fmtTime(Date.now() - S.startedAt) };
      if (!r) return { st: "", sum: S.input.scenario ? S.input.scenario.length + " characters entered" : "Describe the project" };
      return { st: "done", sum: (r.scenario || "").length + " chars, " + (r.answers || []).length + " answers, " + (r.documents || []).length + " docs" };
    }
    if (!r) return { st: S.running ? "running" : "", sum: S.running ? "Waiting for the pipeline" : "Not run yet" };
    if (id === "requirements") {
      var q = (r.quality && r.quality.flags || []).length;
      return { st: q ? "attention" : "done", sum: (r.requirements.functional_requirements || []).length + " FR, " +
        (r.requirements.non_functional_requirements || []).length + " NFR, " + q + " quality flag" + (q === 1 ? "" : "s") };
    }
    if (id === "compliance") {
      var rc = r.compliance_mapping ? r.compliance_mapping.review_count : 0;
      var nr = r.risk_register ? r.risk_register.risks.length : 0;
      return { st: rc ? "attention" : "done", sum: "Risk " + (r.compliance ? r.compliance.risk_level : "-") + ", " + rc + " to review, " + nr + " risks" };
    }
    if (id === "sdlc") {
      var s = r.sdlc_recommendation;
      if (!s) return { st: "", sum: "-" };
      var top = s.scored_models && s.scored_models[0];
      return { st: s.is_close_call ? "attention" : "done", sum: s.recommended_model + (top ? " " + top.percent : "") + (s.is_close_call ? " (close call)" : "") };
    }
    if (id === "artefacts") {
      var a = r.artefacts || {};
      return { st: "done", sum: (a.user_stories || []).length + " stories, " + (a.use_cases || []).length + " use cases, " + (a.open_issues || []).length + " open issues" };
    }
    if (id === "approvals") {
      if (S.sample) return { st: "", sum: "Recorded sample: no stored run" };
      if (!S.run) return { st: "", sum: "Run summary not loaded" };
      var b = S.run.blockers.length;
      return { st: S.run.baseline.status === "frozen" ? "done" : "attention",
               sum: "Baseline " + S.run.baseline.status + (b ? ", " + b + " blocking" : "") };
    }
    return { st: "", sum: "" };
  }

  function renderSidebar() {
    $("stageList").innerHTML = STAGES.map(function (st) {
      var info = stageInfo(st.id);
      return '<li><button type="button" class="stage-btn" data-action="tab" data-tab="' + st.id + '"' +
        (S.tab === st.id ? ' aria-current="true"' : "") + '><span class="stage-dot ' + info.st + '" aria-hidden="true"></span>' +
        '<span class="stage-name">' + esc(st.name) + '</span><span class="stage-sum">' + esc(info.sum) +
        (info.st === "attention" ? '<span class="visually-hidden"> (needs attention)</span>' : "") + "</span></button></li>";
    }).join("");
    var r = S.result, html = "";
    if (r) {
      var v = r.versions || r.meta || {};
      html += (S.sample ? '<div><span class="badge warn">Recorded sample, not a live run</span></div>' : "") +
        "<div>Run <code>" + esc(r.run_id || "-") + "</code></div>" +
        "<div>App " + esc(v.app_version || "-") + " &middot; KB " + esc(v.kb_version || "-") + "</div>" +
        "<div>Embeddings " + esc(v.embedding_model || "-") + "</div>" +
        "<div>Models " + esc((v.models || []).join(", ") || (S.sample ? "none (mocked)" : "-")) + "</div>" +
        (S.lastElapsed != null && !S.sample ? "<div>Took " + fmtTime(S.lastElapsed) + "</div>" : "");
    } else {
      html = "<div>No analysis yet. Enter a scenario, or load the recorded sample.</div>";
    }
    $("runInfo").innerHTML = html;
  }

  function tabsFor() {
    var t = STAGES.slice();
    if (AUDIT_ROLES.indexOf(S.role) >= 0) t.push({ id: "audit", name: "Audit log" });
    return t;
  }
  function renderTabs() {
    var t = tabsFor();
    if (!t.some(function (x) { return x.id === S.tab; })) S.tab = "scenario";
    $("tabs").innerHTML = t.map(function (x) {
      return '<button type="button" class="tab" role="tab" id="tab-' + x.id + '" aria-controls="panel" aria-selected="' + (S.tab === x.id) +
        '" tabindex="' + (S.tab === x.id ? 0 : -1) + '" data-action="tab" data-tab="' + x.id + '">' + esc(x.name) + "</button>";
    }).join("");
    $("panel").setAttribute("aria-labelledby", "tab-" + S.tab);
  }
  function renderBanners() {
    $("banners").innerHTML = S.banners.map(function (b) {
      return '<div class="banner ' + b.kind + '" role="' + (b.kind === "error" ? "alert" : "status") + '"><div>' + b.html +
        '</div><button type="button" class="close" data-action="dismiss" data-id="' + b.id + '" aria-label="Dismiss">&times;</button></div>';
    }).join("");
  }

  function renderPanel() {
    var fn = { scenario: viewScenario, requirements: viewRequirements, compliance: viewCompliance, sdlc: viewSdlc,
               artefacts: viewArtefacts, approvals: viewApprovals, audit: viewAudit }[S.tab];
    try {
      $("panel").innerHTML = fn();
    } catch (e) {   // never a blank screen
      $("panel").innerHTML = '<div class="card"><h3>This view could not be drawn</h3><p class="muted">' + esc(e.message) +
        "</p><p class=\"muted\">The data may be missing a field this view expects.</p></div>";
      if (window.console) console.error(e);
    }
  }
  function needResult(what) {
    return '<div class="card empty"><p>No ' + esc(what) + ' yet.</p><p class="small">Run an analysis from the Scenario tab, or load the recorded sample.</p>' +
      '<div class="btn-row" style="justify-content:center"><button type="button" class="btn" data-action="tab" data-tab="scenario">Go to Scenario</button>' +
      '<button type="button" class="btn btn-ghost" data-action="load-sample">Load recorded sample</button></div></div>';
  }

  // ---------------------------------------------------------------- view: scenario
  function roleOptions(selected) {
    var roles = (S.templates && S.templates.roles) || [];
    if (selected && roles.indexOf(selected) < 0) roles = [selected].concat(roles);
    return roles.map(function (r) { return '<option' + (r === selected ? " selected" : "") + ">" + esc(r) + "</option>"; }).join("");
  }
  function viewScenario() {
    var dis = S.running ? " disabled" : "";
    var h = '<div class="card"><div class="card-head"><h2>Project scenario</h2><span class="hint" id="scenarioCount">' +
      S.input.scenario.length + " / " + MAX_SCENARIO + ' characters</span></div>' +
      '<div class="field"><label for="scenarioText">Describe the system, its users and its constraints</label>' +
      '<textarea id="scenarioText" rows="7" maxlength="' + MAX_SCENARIO + '" placeholder="Example: A digital loan origination system for a mid-sized bank..."' + dis + ">" +
      esc(S.input.scenario) + '</textarea><div class="hint">Emails, phone numbers, card and ID numbers are replaced with placeholders on the server before any model call.</div></div></div>';

    // interview
    h += '<div class="card"><div class="card-head"><h3>Stakeholder interview (optional)</h3><div class="btn-row">' +
      '<button type="button" class="btn btn-sm" data-action="add-answer"' + dis + ">Add an answer</button>" +
      '<button type="button" class="btn btn-sm" data-action="follow-ups"' + (S.running || S.interviewBusy ? " disabled" : "") + ">" +
      (S.interviewBusy ? "Asking..." : "Suggest follow-up questions") + "</button></div></div>" +
      '<p class="hint">Answers can be quoted by requirements. "Suggest follow-up questions" makes one model call (<code>/agents/interview</code>).</p>';
    if (S.input.answers.length) {
      h += S.input.answers.map(function (a, i) {
        return '<div class="answer-row"><select aria-label="Stakeholder role for answer ' + (i + 1) + '" data-answer="' + i + '" data-field="role"' + dis + ">" +
          roleOptions(a.role) + '</select><div><input type="text" style="width:100%;margin-bottom:6px" aria-label="Question ' + (i + 1) +
          '" data-answer="' + i + '" data-field="question" value="' + esc(a.question) + '"' + dis + '><textarea aria-label="Answer ' + (i + 1) +
          '" data-answer="' + i + '" data-field="answer" placeholder="Answer"' + dis + ">" + esc(a.answer) + "</textarea>" +
          (a.question_id ? '<span class="hint">Template ' + esc(a.question_id) + (a.topic ? " &middot; " + esc(a.topic) : "") + "</span>" : "") +
          '</div><button type="button" class="btn btn-sm btn-ghost" data-action="remove-answer" data-i="' + i + '"' + dis + ' aria-label="Remove answer ' + (i + 1) + '">Remove</button></div>';
      }).join("");
    } else {
      h += '<p class="muted small">No answers yet.</p>';
    }
    if (S.followUps) {
      h += "<h4>Suggested follow-ups</h4>";
      if (S.followUps.uncovered_topics && S.followUps.uncovered_topics.length) {
        h += '<p class="small muted">Topics not yet covered: ' + esc(S.followUps.uncovered_topics.join(", ")) + "</p>";
      }
      h += (S.followUps.questions || []).map(function (q, i) {
        return '<div class="q-item"><div class="q-meta">' + esc(q.role) + " &middot; " + esc(q.topic) + (q.template_id ? " &middot; template " + esc(q.template_id) : "") +
          "</div><div>" + esc(q.question) + '</div><div class="hint">' + esc(q.reason) + '</div><button type="button" class="btn btn-sm" data-action="use-followup" data-i="' + i + '"' + dis + ">Answer this</button></div>";
      }).join("") || '<p class="muted small">No follow-up questions returned.</p>';
    }
    if (S.templates) {
      var byRole = {};
      S.templates.templates.forEach(function (t) { (byRole[t.role] = byRole[t.role] || []).push(t); });
      h += '<details><summary class="small">Question templates by stakeholder (' + S.templates.templates.length + ")</summary>" +
        Object.keys(byRole).map(function (role) {
          return "<h4 style=\"margin-top:12px\">" + esc(role) + "</h4>" + byRole[role].map(function (t) {
            return '<div class="q-item"><div class="q-meta">' + esc(t.id) + " &middot; " + esc(t.topic) + "</div><div>" + esc(t.question) +
              '</div><button type="button" class="btn btn-sm" data-action="use-template" data-id="' + esc(t.id) + '"' + dis + ">Answer this</button></div>";
          }).join("");
        }).join("") + "</details>";
    } else {
      h += '<p class="muted small">Question templates are loading (or could not be loaded).</p>';
    }
    h += "</div>";

    // documents
    h += '<div class="card"><div class="card-head"><h3>Documents (optional)</h3><span class="hint">.txt, .md or .csv; up to ' + MAX_DOCS +
      " files of " + MAX_DOC_CHARS.toLocaleString() + ' characters</span></div><input type="file" id="docInput" accept=".txt,.md,.csv,text/plain,text/markdown,text/csv" multiple aria-label="Attach documents"' + dis + ">" +
      '<p class="hint">Files are read in your browser and sent as JSON text; requirements can quote them by file name.</p>';
    if (S.input.documents.length) {
      h += '<ul class="doc-list">' + S.input.documents.map(function (d, i) {
        return "<li><span>" + esc(d.filename) + ' <span class="muted">(' + d.text.length.toLocaleString() + ' characters)</span></span><button type="button" class="btn btn-sm btn-ghost" data-action="remove-doc" data-i="' +
          i + '"' + dis + ">Remove</button></li>";
      }).join("") + "</ul>";
    }
    h += "</div>";

    // run
    h += '<div class="card"><div class="btn-row"><button type="button" class="btn btn-primary" data-action="run"' + dis + '>Run full analysis</button>' +
      '<label class="small" style="font-weight:500"><input type="checkbox" id="artefactsToggle"' + (S.input.artefacts ? " checked" : "") + dis +
      "> Generate artefacts (one extra model call)</label>" +
      '<button type="button" class="btn btn-ghost" data-action="load-sample"' + dis + ">Load recorded sample</button></div>";
    if (S.running) {
      h += '<div class="running-box" role="status"><div class="spinner" aria-hidden="true"></div><div><div class="elapsed" id="elapsed">' +
        fmtTime(Date.now() - S.startedAt) + "</div><div class=\"small muted\">Running the whole pipeline as one request (" + (S.input.artefacts ? "about 8" : "about 7") +
        " model calls). The server reports no per-stage progress, so none is shown. Usually under a minute; the model service may retry.</div></div></div>";
    } else {
      h += '<p class="hint" style="margin-top:10px">One request to <code>/agents/pipeline</code>; results appear in every tab when it finishes.</p>';
    }
    h += "</div>";

    h += '<div class="card"><div class="card-head"><h3>How the pipeline works</h3><div class="legend"><span class="dashed" style="--c:#B26CFF">LLM step (dashed)</span>' +
      '<span style="--c:#9A9AA4">Code step (solid)</span></div></div>' + Charts.pipeline() +
      '<p class="hint" style="margin-top:8px">The SDLC model is chosen by deterministic scoring; the LLM only rates traits (with evidence) and writes the explanation.</p></div>';
    return h;
  }

  // ---------------------------------------------------------------- view: requirements
  function viewRequirements() {
    if (!S.result) return needResult("requirements");
    var r = S.result.requirements, q = S.result.quality || { flags: [], counts: {} };
    var flagsBy = {};
    (q.flags || []).forEach(function (f) { (f.requirement_ids || []).forEach(function (id) { (flagsBy[id] = flagsBy[id] || []).push(f); }); });
    var h = '<div class="card"><div class="card-head"><h2>Requirements</h2><span class="chip accent">' + esc(r.domain_inferred) + "</span></div>" +
      '<div class="grid-3"><div><div class="stat-label">Functional</div><div class="stat">' + (r.functional_requirements || []).length + "</div></div>" +
      '<div><div class="stat-label">Non-functional</div><div class="stat">' + (r.non_functional_requirements || []).length + "</div></div>" +
      '<div><div class="stat-label">Quality flags</div><div class="stat">' + (q.flags || []).length + '</div><div class="small muted">' +
      Object.keys(q.counts || {}).map(function (k) { return esc(k) + " " + q.counts[k]; }).join(" &middot; ") + "</div></div></div>";
    if ((r.assumptions || []).length) h += '<h4 style="margin-top:14px">Project assumptions</h4>' + list(r.assumptions);
    h += "</div>";
    if (S.sample) h += '<div class="notice"><strong>Recorded sample:</strong> there is no stored run, so accept, reject, modify and regenerate are disabled.</div>';
    else if (S.role === "auditor") h += '<div class="notice">The auditor role is read-only; review actions are disabled.</div>';

    h += '<div class="table-wrap"><table><thead><tr><th>ID</th><th>Requirement</th><th>Categories</th><th>Risk / confidence</th><th>Source</th>' +
      "<th>Quality</th><th>Status</th><th>Review</th></tr></thead><tbody>";
    allReqs().forEach(function (x) {
      var it = item("requirement", x.id), flags = flagsBy[x.id] || [];
      var disabled = !live() || S.role === "auditor" || !it || it.status === "superseded";
      var who = canReviewReq(x);
      h += "<tr><td><span class=\"id\">" + esc(x.id) + '</span><div class="small muted">' + esc(x.type) + " &middot; " + esc(x.priority) + " priority</div></td>" +
        "<td>" + esc(x.requirement) +
        (x.acceptance_criteria && x.acceptance_criteria.length ? '<div class="small muted" style="margin-top:6px">Acceptance criteria</div>' + list(x.acceptance_criteria) : "") +
        (x.business_justification ? '<div class="small muted" style="margin-top:6px">Why: ' + esc(x.business_justification) + "</div>" : "") +
        (x.dependencies && x.dependencies.length ? '<div class="small muted">Depends on: ' + esc(x.dependencies.join(", ")) + "</div>" : "") +
        (x.assumptions && x.assumptions.length ? '<div class="small muted">Assumes: ' + esc(x.assumptions.join("; ")) + "</div>" : "") + "</td>" +
        "<td>" + chips(x.categories) + (x.applicable_regulations && x.applicable_regulations.length ? '<div class="small muted" style="margin-top:4px">Regulations</div>' + chips(x.applicable_regulations, "accent") : "") + "</td>" +
        "<td>Risk " + level(x.risk_level) + '<div style="margin-top:4px">Conf. ' + level(x.confidence) + "</div></td>" +
        "<td>" + esc(x.source_stakeholder || "-") +
        (x.source_text ? '<div class="quote">&ldquo;' + esc(x.source_text) + '&rdquo;<span class="src">' + esc(x.source_document ? "from " + x.source_document : "from the scenario or an interview answer") + "</span></div>"
          : '<div class="flag">No supporting quote (inferred)</div>') + "</td>" +
        "<td>" + (flags.length ? flags.map(function (f) {
          return '<span class="badge warn">' + esc(f.type) + '</span><div class="small">' + esc(f.reason) + "</div>" +
            (f.suggested_fix ? '<div class="small muted">Fix: ' + esc(f.suggested_fix) + "</div>" : "") +
            '<div class="small muted">' + esc((f.requirement_ids || []).join(", ")) + " &middot; " + esc(f.detected_by) + "</div>";
        }).join('<div style="height:6px"></div>') : '<span class="muted">-</span>') + "</td>" +
        "<td>" + statusBadge(it ? it.status : x.approval_status) + (it ? '<div class="ver">v' + it.version + (it.history.length > 1 ? " (" + it.history.length + " versions)" : "") + "</div>" : "") + "</td>" +
        '<td><div class="req-actions">' + ["accept", "reject", "modify", "regenerate"].map(function (a) {
          return '<button type="button" class="btn btn-sm" data-action="review" data-type="requirement" data-id="' + esc(x.id) + '" data-act="' + a + '"' +
            (disabled ? " disabled" : "") + ' title="Reviewer: ' + esc(who.join(" or ")) + '">' + a.charAt(0).toUpperCase() + a.slice(1) + "</button>";
        }).join("") + '<span class="ver">' + esc(who.join(" / ")) + "</span></div></td></tr>";
    });
    h += "</tbody></table></div>";
    return h;
  }

  // ---------------------------------------------------------------- view: compliance and risk
  function viewCompliance() {
    if (!S.result) return needResult("compliance analysis");
    var c = S.result.compliance || {}, cm = S.result.compliance_mapping || { requirements: [] }, rr = S.result.risk_register;
    var note = String(cm.notice || "").replace(/^Not a legal determination\.\s*/, "");
    var h = '<div class="notice"><strong>Not a legal determination.</strong> ' + esc(note) + "</div>";
    h += '<div class="card"><div class="card-head"><h2>Compliance</h2><span>Overall risk ' + level(c.risk_level) + "</span></div>" +
      "<p>" + esc(c.summary || "") + "</p>" +
      '<div class="grid-3"><div><div class="stat-label">Knowledge-base coverage</div><div>' + statusBadge(c.kb_relevance) +
      (c.domain_tags && c.domain_tags.length ? '<div class="small muted" style="margin-top:4px">Domain tags: ' + esc(c.domain_tags.join(", ")) + "</div>" : "") + "</div></div>" +
      '<div><div class="stat-label">Sources used / consulted</div><div class="small">' + esc((c.sources_used || []).join(", ") || "-") +
      '</div><div class="small muted">of ' + esc((c.sources_consulted || []).join(", ") || "-") + "</div></div>" +
      '<div><div class="stat-label">Need human review</div><div class="stat">' + (cm.review_count || 0) + "</div></div></div>";
    if (c.kb_relevance && c.kb_relevance !== "relevant") {
      h += '<div class="notice" style="margin-top:12px">The knowledge base does not cover this domain well; findings below are limited to what was retrieved.</div>';
    }
    h += "</div>";

    h += "<h3>Per-requirement mapping</h3>";
    (cm.requirements || []).forEach(function (m) {
      var req = reqById(m.requirement_id);
      h += '<div class="card"><div class="card-head"><div><span class="id" style="font-family:Poppins,sans-serif;font-weight:700;color:#C79CFF">' + esc(m.requirement_id) + "</span> " +
        esc(req ? req.requirement : "") + "</div>" + (m.needs_human_review ? '<span class="badge warn">Needs human review</span>' : '<span class="badge ok">No review flag</span>') + "</div>";
      if (m.review_reasons && m.review_reasons.length) h += '<div class="small" style="color:#F5C46B">' + esc(m.review_reasons.join("; ")) + "</div>";
      if (!m.mappings || !m.mappings.length) h += '<p class="muted small">No knowledge-base content matched this requirement.</p>';
      (m.mappings || []).forEach(function (mp) {
        var it = item("compliance", m.requirement_id + "/" + mp.source);
        var generic = mp.scope !== "domain-specific";
        h += '<div class="card inner" style="margin:10px 0 0"><div class="card-head"><div><strong>' + esc(mp.regulation) + '</strong> <span class="chip' +
          (generic ? "" : " accent") + '">' + esc(generic ? "generic baseline" : "domain-specific") + "</span></div><div>" + statusBadge(mp.status || "no status") +
          " Confidence " + level(mp.confidence) + ' <span class="small muted">distance ' + esc(mp.distance) + "</span></div></div>" +
          '<div class="small muted">' + esc(mp.source) + " &middot; " + esc(mp.jurisdiction) + " &middot; version " + esc(mp.version) +
          (mp.effective_date ? " &middot; KB entry dated " + esc(mp.effective_date) : "") + " &middot; chunk " + esc(mp.chunk_id) + "</div>" +
          (mp.required_control ? '<p style="margin:8px 0 4px"><strong>Required control:</strong> ' + esc(mp.required_control) + "</p>" : "") +
          (mp.compliance_gap ? '<p style="margin:0 0 4px;color:#FF8A8A"><strong>Gap:</strong> ' + esc(mp.compliance_gap) + "</p>" : "") +
          (mp.evidence ? '<div class="quote">&ldquo;' + esc(mp.evidence) + '&rdquo;<span class="src">' + esc(mp.source) + " knowledge-base text (paraphrased principles) &middot; " +
            (mp.evidence_verified ? "quote verified against the retrieved text" : "quote NOT found in the retrieved text") + "</span></div>" : "") +
          (mp.checkpoints && mp.checkpoints.length ? '<div class="small"><strong>Checkpoints:</strong> ' + esc(mp.checkpoints.join("; ")) + "</div>" : "") +
          (mp.retention_and_reporting && mp.retention_and_reporting.length ? '<div class="small"><strong>Retention and reporting:</strong> ' + esc(mp.retention_and_reporting.join("; ")) + "</div>" : "");
        if (generic) {
          h += '<div class="small muted" style="margin-top:6px">Generic baseline: shown for context, not an approval item.</div>';
        } else {
          var dis = !live() || S.role === "auditor" || !it;
          h += '<div class="btn-row" style="margin-top:8px">' + (it ? statusBadge(it.status) + ' <span class="ver">v' + it.version + "</span>" : S.sample ? "" : '<span class="ver">not registered</span>') +
            ["accept", "reject", "modify"].map(function (a) {
              return '<button type="button" class="btn btn-sm" data-action="review" data-type="compliance" data-id="' + esc(m.requirement_id + "/" + mp.source) + '" data-act="' + a + '"' +
                (dis ? " disabled" : "") + ' title="Reviewer: compliance officer">' + a.charAt(0).toUpperCase() + a.slice(1) + "</button>";
            }).join("") + '<span class="ver">reviewer: compliance officer</span></div>';
        }
        h += "</div>";
      });
      h += "</div>";
    });
    if ((c.dropped_considerations || []).length) {
      h += '<div class="card"><h3>Dropped model claims</h3><p class="small muted">Considerations the model returned that could not be tied to retrieved text; they are not shown above.</p>' +
        list(c.dropped_considerations.map(function (d) { return typeof d === "string" ? d : JSON.stringify(d); })) + "</div>";
    }

    h += '<div class="card"><h3>Retrieved knowledge-base chunks</h3><div class="table-wrap"><table><thead><tr><th>Chunk</th><th>Source</th><th>Scope</th><th>Distance</th><th>Matched requirements</th><th>Applies to domain</th></tr></thead><tbody>' +
      (c.retrieved_chunks || []).map(function (k) {
        return "<tr><td><code>" + esc(k.id) + "</code></td><td>" + esc(k.source) + "</td><td>" + esc(k.scope) + "</td><td>" + esc(k.distance) + "</td><td>" +
          esc((k.matched_requirement_ids || []).join(", ")) + "</td><td>" + (k.applies_to_domain ? "yes" : "no") + "</td></tr>";
      }).join("") + '</tbody></table></div><p class="hint" style="margin-top:6px">Squared-L2 distance; lower is closer. Matches above 0.62 are discarded.</p></div>';

    if (rr) {
      var risks = (rr.risks || []).slice().sort(function (a, b) { return b.score - a.score || a.rank - b.rank; });
      var bl = (rr.summary && rr.summary.by_level) || {};
      h += '<div class="card"><div class="card-head"><h2>Risk register</h2><span class="small muted">' + esc(rr.scoring || "") + "</span></div>" +
        '<div class="grid-2" style="align-items:start"><div style="max-width:380px">' + Charts.riskGrid(risks) + '<div class="heat-legend" style="margin-top:6px">score ' +
        Charts.colors.heat.map(function (c2, i) { return '<i style="background:' + c2 + '" title="band ' + (i + 1) + '"></i>'; }).join("") + " higher</div></div>" +
        '<div class="grid-3"><div><div class="stat-label">High</div><div class="stat">' + (bl.High || 0) + '</div></div><div><div class="stat-label">Medium</div><div class="stat">' +
        (bl.Medium || 0) + '</div></div><div><div class="stat-label">Low</div><div class="stat">' + (bl.Low || 0) + "</div></div></div></div>" +
        '<div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>Risk</th><th>Description</th><th>Category</th><th>Likelihood</th><th>Impact</th><th>Score</th><th>Mitigation</th><th>Requirements</th></tr></thead><tbody>' +
        risks.map(function (x) {
          return '<tr><td><span class="id">' + esc(x.id) + '</span><div class="ver">rank ' + esc(x.rank) + "</div></td><td>" + esc(x.description) + "</td><td>" + esc(x.category) +
            "</td><td><strong>" + x.likelihood + '</strong><div class="small muted">' + esc(x.likelihood_reason) + "</div></td><td><strong>" + x.impact +
            '</strong><div class="small muted">' + esc(x.impact_reason) + "</div></td><td><strong>" + x.score + "</strong> " + level(x.level) + "</td><td>" + esc(x.mitigation) +
            "</td><td>" + esc((x.linked_requirement_ids || []).join(", ")) + "</td></tr>";
        }).join("") + "</tbody></table></div></div>";
    }
    return h;
  }

  // ---------------------------------------------------------------- view: SDLC
  function viewSdlc() {
    if (!S.result || !S.result.sdlc_recommendation) return needResult("SDLC recommendation");
    var s = S.result.sdlc_recommendation, models = s.scored_models || [], top = models[0] || {};
    var winner = models.filter(function (m) { return m.model === s.recommended_model; })[0] || top;
    var it = item("sdlc", "sdlc");
    var override = s.human_override || (it && it.content && it.content.human_override ? it.content.recommended_model : null);
    var h = '<div class="card"><div class="hero"><div><div class="hero-label">Recommended SDLC model</div><div class="hero-model">' + esc(s.recommended_model) +
      '</div><div class="small muted">Deterministic fit score against fixed model profiles; the LLM rated the traits but did not choose the model.</div>' +
      (override ? '<div class="banner warn" style="margin-top:10px"><div>Reviewers changed the selection to <strong>' + esc(override) + "</strong> (human override).</div></div>" : "") +
      '</div><div style="text-align:right"><div class="hero-pct">' + esc(winner.percent || "") + '</div><div class="stat-label">fit score</div></div></div></div>';
    if (s.is_close_call && models[1]) {
      h += '<div class="banner warn" style="margin-bottom:16px"><div><strong>Close call:</strong> ' + esc(models[0].model + " " + models[0].percent + " vs " + models[1].model + " " + models[1].percent) +
        ". The top two are within 5 points; treat both as reasonable and let the approvers decide.</div></div>";
    }
    var j = s.justification || {};
    h += '<div class="card"><h3>Why this model</h3><div class="grid-2">' +
      [["Requirement characteristics", j.requirement_characteristics], ["Project characteristics", j.project_characteristics],
       ["Engineering justification", j.engineering_justification], ["Key trade-offs", j.key_tradeoffs]].map(function (p) {
        return "<div><h4>" + esc(p[0]) + "</h4><p>" + esc(p[1] || "-") + "</p></div>";
      }).join("") + "</div></div>";

    h += '<div class="grid-2 charts-row"><div class="card"><h3>Traits vs ' + esc(winner.model) + ' profile</h3>' +
      Charts.radar(TRAIT_KEYS, TRAIT_LABELS, s.traits || {}, winner.profile, winner.model) +
      '<div class="legend"><span style="--c:#B26CFF">This project (rated 1-5)</span><span class="dashed" style="--c:#C79CFF">' + esc(winner.model) + " typical profile</span></div></div>" +
      '<div class="card"><h3>Ranking of all seven models</h3>' + Charts.bars(models, s.recommended_model) +
      '<p class="hint">Scores are a 0-100 weighted fit, not probabilities.</p></div></div>';
    h += '<div class="card"><h3>Why not the others</h3><ul class="why-list">' +
      models.filter(function (m) { return m.why_not; }).map(function (m) {
        return "<li><strong>" + esc(m.model) + "</strong> (" + esc(m.percent) + "): " + esc(m.why_not) + "</li>";
      }).join("") + "</ul></div>";

    h += '<div class="card"><div class="card-head"><h3>Trait-by-model heat matrix</h3><div class="heat-legend">1 ' +
      Charts.colors.heat.map(function (c2, i) { return '<i style="background:' + c2 + '" title="' + (i + 1) + '"></i>'; }).join("") + ' 5</div></div><div style="overflow-x:auto">' +
      Charts.heatMatrix(TRAIT_KEYS, TRAIT_LABELS, s.traits || {}, models, s.recommended_model) + "</div></div>";

    var td = s.trait_details || {};
    h += '<div class="card"><h3>Trait ratings and evidence</h3><div class="table-wrap"><table><thead><tr><th>Trait</th><th>Score</th><th>Evidence</th><th>Reason</th></tr></thead><tbody>' +
      TRAIT_KEYS.map(function (k) {
        var d = td[k] || { score: (s.traits || {})[k] };
        return "<tr><td>" + esc(TRAIT_LABELS[k]) + "</td><td><strong>" + esc(d.score) + "</strong>/5</td><td>" + esc(d.evidence || "-") + "</td><td>" + esc(d.reason || "-") + "</td></tr>";
      }).join("") + "</tbody></table></div></div>";

    h += sdlcApprovalCard(s, it);
    h += workflowCard(S.result.sdlc_workflow);
    return h;
  }

  function sdlcApprovalCard(s, it) {
    var ar = s.approval_required || {};
    var accepted = it ? it.accepted_by || [] : ar.approved_by || [];
    var h = '<div class="card"><div class="card-head"><h3>SDLC approval (four approvers)</h3>' + statusBadge(it ? it.status : ar.status) + "</div>";
    h += '<div class="approver-grid">' + SDLC_APPROVERS.map(function (r) {
      var yes = accepted.indexOf(r) >= 0;
      return '<div class="approver' + (yes ? " yes" : "") + '"><div class="who">' + esc(r.charAt(0).toUpperCase() + r.slice(1)) + '</div><div class="small ' + (yes ? "" : "muted") + '">' +
        (yes ? "Accepted" : "Awaiting") + "</div></div>";
    }).join("") + "</div>";
    if (S.sample) h += '<p class="small muted" style="margin-top:10px">Recorded sample: approvals are not stored, so all four show as awaiting.</p>';
    else if (ar.note && !it) h += '<p class="small muted" style="margin-top:10px">' + esc(ar.note) + "</p>";
    if (it) {
      var dis = !live() || SDLC_APPROVERS.indexOf(S.role) < 0;
      h += '<div class="btn-row" style="margin-top:12px"><span class="ver">v' + it.version + "</span>" + ["accept", "reject", "modify", "regenerate"].map(function (a) {
        return '<button type="button" class="btn btn-sm" data-action="review" data-type="sdlc" data-id="sdlc" data-act="' + a + '"' + (dis ? " disabled" : "") + ">" +
          a.charAt(0).toUpperCase() + a.slice(1) + "</button>";
      }).join("") + '<span class="ver">' + (SDLC_APPROVERS.indexOf(S.role) < 0 ? "Your role is not one of the four approvers." : "Acting as " + esc(S.role)) + "</span></div>";
    }
    return h + "</div>";
  }

  function wfCell(items) {
    if (!items || !items.length) return '<span class="muted">-</span>';
    return "<ul>" + items.map(function (x) {
      var added = x.source && x.source !== "base";
      return "<li>" + esc(x.text) + (added ? ' <span class="chip accent" title="' + esc(x.reason || "") + '">added' + (x.requirement_ids && x.requirement_ids.length ? ": " + esc(x.requirement_ids.join(", ")) : "") + "</span>" : "") + "</li>";
    }).join("") + "</ul>";
  }
  function workflowCard(w) {
    if (!w) return "";
    var cols = [["activities", "Activities"], ["responsible_roles", "Roles"], ["deliverables", "Deliverables"], ["security_activities", "Security"],
                ["testing_requirements", "Testing"], ["compliance_checkpoints", "Compliance checkpoints"], ["approval_gates", "Approval gates"],
                ["entry_criteria", "Entry criteria"], ["exit_criteria", "Exit criteria"], ["traceability_requirements", "Traceability"]];
    return '<div class="card"><div class="card-head"><h3>' + esc(w.model) + ' workflow</h3><span class="small muted">' + esc(w.added_items || 0) + " project-specific items added; linked requirements: " +
      esc((w.requirements_linked || []).join(", ") || "-") + '</span></div><p class="small muted">' + esc(w.notice || "") + '</p><div class="table-wrap"><table class="wide"><thead><tr><th>#</th><th>Phase</th>' +
      cols.map(function (c) { return "<th>" + c[1] + "</th>"; }).join("") + "</tr></thead><tbody>" +
      (w.phases || []).map(function (p) {
        return "<tr><td>" + esc(p.order) + "</td><td><strong>" + esc(p.name) + "</strong></td>" + cols.map(function (c) { return "<td>" + wfCell(p[c[0]]) + "</td>"; }).join("") + "</tr>";
      }).join("") + "</tbody></table></div></div>";
  }

  // ---------------------------------------------------------------- view: artefacts
  function srcList(ss) {
    if (!ss || !ss.length) return "";
    return '<div class="small muted" style="margin-top:6px">Sources:</div>' + ss.map(function (s) {
      return '<div class="quote">' + (s.statement ? "&ldquo;" + esc(s.statement) + "&rdquo;" : "(no quote; inferred)") + '<span class="src">' + esc(s.requirement_id) + " &middot; " + esc(s.stakeholder) +
        (s.assumptions && s.assumptions.length ? " &middot; assumes " + esc(s.assumptions.join("; ")) : "") + "</span></div>";
    }).join("");
  }
  function table(headers, rows) {
    if (!rows.length) return '<p class="muted">None generated.</p>';
    return '<div class="table-wrap"><table><thead><tr>' + headers.map(function (x) { return "<th>" + esc(x) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      rows.map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>"; }).join("") + "</tbody></table></div>";
  }
  function ids(a) { return esc((a || []).join(", ") || "-"); }
  function viewArtefacts() {
    if (!S.result || !S.result.artefacts) return needResult("artefacts");
    var a = S.result.artefacts, h = '<div class="card"><div class="card-head"><h2>Artefacts and SRS</h2><div class="btn-row">' +
      '<button type="button" class="btn btn-primary" data-action="srs-md">Download SRS (Markdown)</button><button type="button" class="btn" data-action="srs-html">Open printable SRS</button></div></div>' +
      '<p class="small muted">' + esc(a.generation ? a.generation.note : "") + (S.sample ? " The sample SRS is rebuilt from this recorded result (POST /agents/srs, no model call)." : " The SRS reflects the current approval state of the stored run.") + "</p></div>";
    h += '<div class="subtabs" role="tablist" aria-label="Artefact types">' + ART_TABS.map(function (t) {
      return '<button type="button" class="subtab" role="tab" aria-selected="' + (S.artTab === t.id) + '" data-action="art-tab" data-tab="' + t.id + '">' + esc(t.name) + "</button>";
    }).join("") + "</div>";
    var t = S.artTab;
    if (t === "stories") {
      h += (a.user_stories || []).map(function (u) {
        return '<div class="card"><div class="card-head"><span class="id" style="font-family:Poppins;font-weight:700;color:#C79CFF">' + esc(u.id) + '</span><span class="small muted">' + ids(u.requirement_ids) + "</span></div><p><strong>" + esc(u.story) +
          '</strong></p><div class="small muted">Acceptance criteria</div>' + list(u.acceptance_criteria) + srcList(u.source_statements) + "</div>";
      }).join("") || '<p class="muted">None generated.</p>';
    } else if (t === "usecases") {
      h += (a.use_cases || []).map(function (u) {
        return '<div class="card"><div class="card-head"><h3>' + esc(u.id + " " + u.name) + '</h3><span class="small muted">' + ids(u.requirement_ids) + "</span></div><p>Primary actor: <strong>" + esc(u.primary_actor) +
          '</strong></p><div class="grid-2"><div><h4>Preconditions</h4>' + list(u.preconditions) + "<h4>Main flow</h4><ol>" + (u.main_flow || []).map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("") +
          "</ol></div><div><h4>Alternative flows</h4>" + list(u.alternative_flows) + "<h4>Postconditions</h4>" + list(u.postconditions) + "</div></div>" + srcList(u.source_statements) + "</div>";
      }).join("") || '<p class="muted">None generated.</p>';
    } else if (t === "workflows") {
      h += (a.process_workflows || []).map(function (w) {
        return '<div class="card"><div class="card-head"><h3>' + esc(w.id + " " + w.name) + '</h3><span class="small muted">' + ids(w.requirement_ids) + "</span></div><ol>" +
          (w.steps || []).map(function (s) { return "<li><strong>" + esc(s.actor) + ":</strong> " + esc(s.action) + "</li>"; }).join("") + "</ol>" + srcList(w.source_statements) + "</div>";
      }).join("") || '<p class="muted">None generated.</p>';
    } else if (t === "data") {
      h += "<h3>Data requirements</h3>" + table(["ID", "Entity", "Description", "Attributes", "Retention", "Requirements"], (a.data_requirements || []).map(function (d) {
        return ['<span class="id">' + esc(d.id) + "</span>", esc(d.entity), esc(d.description) + srcList(d.source_statements), list(d.attributes), esc(d.retention || "-"), ids(d.requirement_ids)];
      }));
      h += '<h3 style="margin-top:18px">Interface requirements</h3>' + table(["ID", "Name", "Type", "Description", "Requirements"], (a.interface_requirements || []).map(function (d) {
        return ['<span class="id">' + esc(d.id) + "</span>", esc(d.name), esc(d.interface_type), esc(d.description) + srcList(d.source_statements), ids(d.requirement_ids)];
      }));
    } else if (t === "rtm") {
      h += '<p class="small muted">Built by code from the records, compliance mapping, risk register, workflow and artefacts.</p>' +
        table(["Requirement", "Source", "Compliance", "Risks", "Stories", "Use cases", "Workflows", "Data", "Interfaces", "Workflow items", "Status"], (a.traceability_matrix || []).map(function (r) {
          return ['<span class="id">' + esc(r.requirement_id) + '</span> <span class="small muted">' + esc(r.type) + '</span><div class="small">' + esc(r.requirement) + "</div>",
            r.source ? esc(r.source.stakeholder) + (r.source.statement ? '<div class="quote">&ldquo;' + esc(r.source.statement) + "&rdquo;</div>" : "") : "-",
            (r.compliance_sources || []).map(function (c) { return esc(c.source) + " " + statusBadge(c.status) + " " + level(c.confidence); }).join("<br>") || "-",
            ids(r.risk_ids), ids(r.user_stories_ids), ids(r.use_cases_ids), ids(r.process_workflows_ids), ids(r.data_requirements_ids), ids(r.interface_requirements_ids),
            list(r.workflow_items), statusBadge(item("requirement", r.requirement_id) ? item("requirement", r.requirement_id).status : r.approval_status)];
        }));
    } else if (t === "controls") {
      h += '<p class="small muted">Not a legal determination. Built from the compliance mapping; the knowledge base is paraphrased principles, not law text.</p>' +
        table(["Requirement", "Regulation", "Scope", "Required control", "Status", "Gap", "Evidence", "Checkpoints / retention", "Confidence"], (a.compliance_control_matrix || []).map(function (c) {
          return ['<span class="id">' + esc(c.requirement_id) + "</span>", esc(c.regulation) + '<div class="small muted">' + esc(c.jurisdiction) + " &middot; v" + esc(c.version) + "</div>",
            esc(c.scope), esc(c.required_control || "-"), statusBadge(c.status), esc(c.compliance_gap || "-"),
            c.evidence ? '<div class="quote">&ldquo;' + esc(c.evidence) + '&rdquo;<span class="src">' + (c.evidence_verified ? "verified" : "not verified") + "</span></div>" : "-",
            list((c.checkpoints || []).concat(c.retention_and_reporting || [])), level(c.confidence) + (c.needs_human_review ? '<div class="flag">needs review</div>' : "")];
        }));
    } else if (t === "assumptions") {
      var ar = a.assumptions_register || {};
      h += "<h3>Assumptions</h3>" + table(["ID", "Assumption", "Scope", "Requirements"], (ar.assumptions || []).map(function (x) {
        return ['<span class="id">' + esc(x.id) + "</span>", esc(x.text), esc(x.scope), ids(x.requirement_ids)];
      }));
      h += '<h3 style="margin-top:18px">Dependencies</h3>' + table(["Requirement", "Depends on"], (ar.dependencies || []).map(function (d) {
        return ['<span class="id">' + esc(d.requirement_id) + "</span>", ids(d.depends_on)];
      }));
    } else if (t === "issues") {
      h += table(["ID", "Type", "Description", "Requirements", "Origin"], (a.open_issues || []).map(function (x) {
        return ['<span class="id">' + esc(x.id) + "</span>", '<span class="badge warn">' + esc(x.type) + "</span>", esc(x.description), ids(x.requirement_ids), esc(x.origin)];
      }));
    }
    return h;
  }

  // ---------------------------------------------------------------- view: approvals
  function viewApprovals() {
    if (!S.result) return needResult("run to approve");
    if (S.sample) {
      return '<div class="card"><h2>Approvals and baseline</h2><p>This is the recorded sample. It has no stored run on this server, so there is nothing to accept, freeze or audit.</p>' +
        '<p class="muted small">Run a live analysis to review requirements (business analyst or product owner; high-risk security requirements: security officer), compliance interpretations ' +
        "(compliance officer) and the SDLC selection (project manager, architect, security officer and compliance officer), then freeze the baseline (product owner or business analyst).</p></div>";
    }
    if (!S.run) return '<div class="card"><p>The run summary could not be loaded.</p><button type="button" class="btn" data-action="refresh-run">Try again</button></div>';
    var b = S.run.baseline, frozen = b.status === "frozen";
    var h = '<div class="card"><div class="card-head"><h2>Requirement baseline</h2>' + statusBadge(b.status) + "</div>" +
      (frozen ? "<p>Frozen " + esc(b.frozen_at) + " by " + esc(b.frozen_by) + ". Any later change to an item reopens it.</p>"
        : "<p>The baseline can be frozen once every mandatory item (each requirement and each domain-specific compliance mapping) is approved. The SDLC selection is approved separately and does not block it.</p>") +
      '<div class="btn-row"><button type="button" class="btn btn-primary" data-action="freeze"' + (frozen ? " disabled" : "") + ">Freeze baseline</button>" +
      '<button type="button" class="btn btn-ghost" data-action="refresh-run">Refresh</button><span class="small muted">Freezing needs the product owner or business analyst role.</span></div></div>';
    h += '<div class="card"><h3>Blocking items (' + S.run.blockers.length + ")</h3>" + (S.run.blockers.length ? table(["Type", "Item", "Version", "Status"], S.run.blockers.map(function (x) {
      return [esc(x.item_type), '<span class="id">' + esc(x.item_id) + "</span>", "v" + esc(x.version), statusBadge(x.status)];
    })) : '<p class="muted">Nothing is blocking the baseline.</p>') + "</div>";
    h += '<div class="card"><h3>All approval items</h3>' + table(["Type", "Item", "Version", "Status", "Mandatory", "Regenerations left", "History"], S.run.items.map(function (x) {
      return [esc(x.item_type), '<span class="id">' + esc(x.item_id) + "</span>", "v" + esc(x.version), statusBadge(x.status) + (x.item_type === "sdlc" && x.accepted_by ? '<div class="small muted">accepted by ' + esc(x.accepted_by.join(", ") || "nobody yet") + "</div>" : ""),
        x.mandatory ? "yes" : "no", esc(x.regenerations_left), esc(x.history.map(function (v) { return "v" + v.version + " " + v.status; }).join(", "))];
    })) + "</div>";
    h += '<div class="card"><h3>Decision history</h3>' + table(["When", "Role", "Action", "Item", "Version", "Comment"], (S.run.decisions || []).map(function (d) {
      return [esc(d.created_at), esc(d.role), esc(d.action), esc(d.item_type + " " + d.item_id), esc(d.version), esc(d.action === "freeze" ? "(snapshot of item versions)" : d.comment || "")];
    })) + "</div>";
    return h;
  }

  // ---------------------------------------------------------------- view: audit
  function viewAudit() {
    var f = S.auditFilters;
    var h = '<div class="card"><div class="card-head"><h2>Audit log</h2><span class="small muted">Read-only; append-only and hash-chained. Viewing it is itself logged.</span></div>' +
      '<form id="auditForm" class="btn-row" style="align-items:flex-end"><div class="field"><label for="fRun">Run id</label><input type="text" id="fRun" value="' + esc(f.run_id) + '" placeholder="any"></div>' +
      '<div class="field"><label for="fRole">Role</label><select id="fRole"><option value="">any</option>' + ROLES.concat(["unspecified", "system"]).map(function (r) {
        return "<option" + (f.role === r ? " selected" : "") + ">" + esc(r) + "</option>";
      }).join("") + '</select></div><div class="field"><label for="fAction">Action</label><input type="text" id="fAction" value="' + esc(f.action) + '" placeholder="e.g. approval_accept"></div>' +
      '<div class="field"><label for="fLimit">Limit</label><input type="text" id="fLimit" value="' + esc(f.limit) + '" size="5" inputmode="numeric"></div>' +
      '<div class="field"><button type="submit" class="btn btn-primary">Load events</button></div>' +
      (S.result && S.result.run_id && !S.sample ? '<div class="field"><button type="button" class="btn btn-ghost" data-action="audit-this-run">This run only</button></div>' : "") + "</form></div>";
    if (!S.audit) return h + '<p class="muted">Load events to see the log and the chain check.</p>';
    var c = S.audit.chain;
    h += '<div class="banner ' + (c.ok ? "" : "error") + '" style="margin-bottom:14px"><div>' + (c.ok ? "<strong>Hash chain verified:</strong> all " + esc(c.checked) + " events in the log match their hashes."
      : "<strong>Hash chain broken</strong> at event " + esc(c.first_bad_seq) + " after " + esc(c.checked) + " good events: " + esc(c.reason)) + "</div></div>";
    h += table(["Seq", "Time", "Run", "Role", "Action", "Stage", "Model", "Versions", "Details"], S.audit.events.map(function (e) {
      var d = JSON.stringify(e.details, null, 2);
      return [esc(e.seq), esc(e.ts), "<code>" + esc(e.run_id || "-") + "</code>", esc(e.role), "<strong>" + esc(e.action) + "</strong>", esc(e.stage || "-"), esc(e.model || "-"),
        '<span class="small">' + esc(e.app_version) + " / " + esc(e.kb_version) + "</span>",
        '<details><summary class="small">' + esc(d.length) + ' chars</summary><pre class="mono" style="white-space:pre-wrap;max-width:520px;max-height:300px;overflow:auto">' + esc(d.slice(0, 6000)) + (d.length > 6000 ? "\n..." : "") + "</pre></details>"];
    }));
    return h;
  }

  // ---------------------------------------------------------------- actions
  function runPipeline() {
    var sc = S.input.scenario.trim();
    if (sc.length < 5) { showError(new ApiError(400, "Enter a scenario of at least a few words first."), "scenario"); return; }
    var empty = S.input.answers.filter(function (a) { return !a.answer.trim() || !a.question.trim(); });
    if (empty.length) { showError(new ApiError(400, "Every interview answer needs a question and an answer (remove empty rows)."), "interview"); return; }
    clearBanners("error");
    S.running = true; S.startedAt = Date.now();
    S.timer = setInterval(function () {
      var el = $("elapsed"); if (el) el.textContent = fmtTime(Date.now() - S.startedAt);
      renderSidebar();
    }, 500);
    render();
    var body = { scenario: S.input.scenario, answers: S.input.answers.map(function (a) {
      return { role: a.role, question: a.question, answer: a.answer, question_id: a.question_id || null, topic: a.topic || null };
    }), documents: S.input.documents };
    api("POST", "/agents/pipeline" + (S.input.artefacts ? "" : "?artefacts=false"), body).then(function (data) {
      S.result = data; S.sample = false; S.run = null; S.audit = null;
      S.banners = S.banners.filter(function (b) { return b.key !== "sample"; });
      maskingBanner(data.masking);
      history.replaceState(null, "", "#run=" + encodeURIComponent(data.run_id));
      return loadRun().then(function () { S.tab = "requirements"; });
    }).catch(function (e) { showError(e, "pipeline"); }).then(function () {
      clearInterval(S.timer); S.lastElapsed = Date.now() - S.startedAt; S.running = false; render();
    });
  }
  function maskingBanner(m) {
    if (m && m.total) {
      addBanner("warn", "<strong>Sensitive data masked:</strong> " + esc(Object.keys(m.counts).map(function (k) { return m.counts[k] + " " + k; }).join(", ")) +
        " in " + esc((m.fields || []).join(", ")) + ". Classification: " + esc(m.classification) + ". " + esc(m.note || ""), "masking");
    } else {
      S.banners = S.banners.filter(function (b) { return b.key !== "masking"; });
    }
  }
  function openRun(id) {
    return Promise.all([api("GET", "/runs/" + encodeURIComponent(id) + "/result"), api("GET", "/runs/" + encodeURIComponent(id))])
      .then(function (out) {
        S.result = out[0]; if (!S.result.run_id) S.result.run_id = id;
        S.run = out[1]; S.sample = false; S.audit = null;
        S.input.scenario = S.result.scenario || ""; S.input.documents = [];
        S.input.answers = (S.result.answers || []).map(function (a) { return { role: a.role, question: a.question, answer: a.answer, question_id: a.question_id, topic: a.topic }; });
        if (S.tab === "scenario") S.tab = "requirements";
        render();
      }).catch(function (e) { showError(e, "opening stored run " + id); history.replaceState(null, "", location.pathname); });
  }
  function loadRun() {
    if (!S.result || !S.result.run_id || S.sample) return Promise.resolve();
    return api("GET", "/runs/" + encodeURIComponent(S.result.run_id)).then(function (run) { S.run = run; })
      .catch(function (e) { S.run = null; showError(e, "loading the run summary"); });
  }
  function refreshAll() {
    if (!live()) return Promise.resolve();
    return Promise.all([api("GET", "/runs/" + encodeURIComponent(S.result.run_id) + "/result"), api("GET", "/runs/" + encodeURIComponent(S.result.run_id))])
      .then(function (out) {
        var keep = { run_id: S.result.run_id, masking: S.result.masking, srs: S.result.srs, meta: S.result.meta };
        S.result = out[0]; Object.keys(keep).forEach(function (k) { if (!(k in S.result)) S.result[k] = keep[k]; });
        S.run = out[1];
      }).catch(function (e) { showError(e, "refreshing"); }).then(render);
  }
  function loadSample() {
    if (S.result && !confirmReplace()) return;
    api("GET", "/static/sample_run.json").then(function (data) {
      S.result = data; S.sample = true; S.run = null; S.audit = null; S.followUps = null;
      history.replaceState(null, "", location.pathname);
      S.input.scenario = data.scenario || ""; S.input.documents = [];
      S.input.answers = (data.answers || []).map(function (a) { return { role: a.role, question: a.question, answer: a.answer, question_id: a.question_id, topic: a.topic }; });
      clearBanners("error");
      addBanner("warn", "<strong>" + esc((data._sample && data._sample.label) || "Recorded sample, not a live run") + ".</strong> " + esc((data._sample && data._sample.note) || ""), "sample");
      maskingBanner(data.masking);
      S.tab = "sdlc"; render(); window.scrollTo(0, 0);
    }).catch(function (e) { showError(e, "loading the recorded sample"); });
  }
  function confirmReplace() { return window.confirm("Replace the current results? They stay stored on the server (if live) but leave this page."); }
  function newAnalysis() {
    if (S.running) return;
    if ((S.result || S.input.scenario) && !window.confirm("Start a new analysis? This clears the scenario and results on this page.")) return;
    S.result = null; S.run = null; S.sample = false; S.audit = null; S.followUps = null; S.lastElapsed = null;
    S.input = { scenario: "", answers: [], documents: [], artefacts: true };
    S.banners = []; S.tab = "scenario"; render();
    history.replaceState(null, "", location.pathname);
    var t = $("scenarioText"); if (t) t.focus();
  }
  function loadTemplates() {
    return fetch("/agents/interview/templates", { headers: { "X-Role": S.role } }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { S.templates = d; if (S.tab === "scenario" && !S.running) renderPanel(); }).catch(function () { S.templates = null; });
  }
  function followUps() {
    if (S.input.scenario.trim().length < 5) { showError(new ApiError(400, "Enter the scenario first; follow-up questions are based on it."), "interview"); return; }
    S.interviewBusy = true; renderPanel();
    api("POST", "/agents/interview", { scenario: S.input.scenario, answers: S.input.answers.filter(function (a) { return a.answer.trim() && a.question.trim(); }) })
      .then(function (d) { S.followUps = d; maskingBanner(d.masking); })
      .catch(function (e) { showError(e, "interview"); })
      .then(function () { S.interviewBusy = false; renderPanel(); });
  }
  function addAnswer(a) {
    var roles = (S.templates && S.templates.roles) || [];
    S.input.answers.push(a || { role: roles[0] || "", question: "", answer: "", question_id: null, topic: null });
    renderPanel();
    var boxes = document.querySelectorAll('textarea[data-field="answer"]');
    if (boxes.length) boxes[boxes.length - 1].focus();
  }
  function readFiles(files) {
    var arr = Array.prototype.slice.call(files || []);
    arr.forEach(function (f) {
      var ext = (f.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
      if (DOC_EXT.indexOf(ext) < 0) { showError(new ApiError(400, f.name + ": only .txt, .md and .csv files can be attached."), "documents"); return; }
      if (S.input.documents.length >= MAX_DOCS) { showError(new ApiError(400, "At most " + MAX_DOCS + " documents per analysis."), "documents"); return; }
      var rd = new FileReader();
      rd.onload = function () {
        var text = String(rd.result || "");
        if (text.length > MAX_DOC_CHARS) { showError(new ApiError(400, f.name + " has " + text.length.toLocaleString() + " characters; the limit is " + MAX_DOC_CHARS.toLocaleString() + "."), "documents"); return; }
        if (!text.trim()) { showError(new ApiError(400, f.name + " is empty."), "documents"); return; }
        if (S.input.documents.length >= MAX_DOCS) return;
        S.input.documents.push({ filename: f.name, text: text });
        renderPanel();
      };
      rd.onerror = function () { showError(new ApiError(400, "Could not read " + f.name + "."), "documents"); };
      rd.readAsText(f);
    });
  }

  function srs(format) {
    if (!S.result) return;
    var win = format === "html" ? window.open("", "_blank") : null;   // opened synchronously so pop-up blockers allow it
    if (win) win.document.write("<p style=\"font-family:sans-serif\">Preparing the SRS...</p>");
    var p = S.sample || !S.result.run_id ? postSrs(format) :
      fetchText("GET", "/agents/srs/" + encodeURIComponent(S.result.run_id) + "?format=" + format).catch(function (e) {
        if (e.status === 404) return postSrs(format);   // run no longer stored: rebuild from the result we have
        throw e;
      });
    p.then(function (text) {
      var blob = new Blob([text], { type: format === "html" ? "text/html" : "text/markdown" });
      var url = URL.createObjectURL(blob);
      if (win) { win.location.href = url; }
      else {
        // Markdown, or the HTML version when the browser blocked the new window: save as a file instead.
        var a = document.createElement("a"); a.href = url; a.download = "requora-srs-" + (S.sample ? "sample" : S.result.run_id) + (format === "html" ? ".html" : ".md");
        document.body.appendChild(a); a.click(); a.remove();
      }
      setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
    }).catch(function (e) { if (win) win.close(); showError(e, "SRS"); });
  }
  function postSrs(format) {
    var body = JSON.parse(JSON.stringify(S.result)); delete body._sample;
    return fetchText("POST", "/agents/srs?format=" + format, body);
  }
  function fetchText(method, path, body) {
    var opts = { method: method, headers: { "X-Role": S.role } };
    if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    return fetch(path, opts).catch(function (e) { throw new ApiError(0, e.message); }).then(function (res) {
      if (res.ok) return res.text();
      return res.json().catch(function () { return {}; }).then(function (d) { throw new ApiError(res.status, detailText(d.detail) || res.statusText, d.detail); });
    });
  }

  function freeze() {
    api("POST", "/runs/" + encodeURIComponent(S.result.run_id) + "/baseline/freeze").then(function () {
      addBanner("", "<strong>Baseline frozen.</strong> Any later change to an item reopens it.", "freeze");
      return refreshAll();
    }).catch(function (e) { showError(e, "freeze baseline"); refreshAll(); });
  }
  function loadAudit() {
    var f = S.auditFilters, q = [];
    ["run_id", "role", "action"].forEach(function (k) { if (f[k]) q.push(k + "=" + encodeURIComponent(f[k])); });
    var lim = parseInt(f.limit, 10); q.push("limit=" + (lim >= 1 && lim <= 5000 ? lim : 200));
    api("GET", "/audit?" + q.join("&")).then(function (d) { S.audit = d; renderPanel(); }).catch(function (e) { showError(e, "audit log"); });
  }

  // ---------------------------------------------------------------- review dialog
  function openReview(type, id, act) {
    var it = item(type, id); if (!it) return;
    var d = $("dialog"), c = it.content || {}, h = '<form method="dialog" id="reviewForm"><h3 id="dialogTitle">' + esc(act.charAt(0).toUpperCase() + act.slice(1)) + " " + esc(type) + " " + esc(id) +
      ' <span class="ver">v' + it.version + " &middot; " + esc(it.status) + "</span></h3>";
    if (type === "requirement") h += '<p class="small">' + esc(c.requirement) + "</p>";
    if (type === "compliance") h += '<p class="small">' + esc(c.required_control || "") + "</p>";
    if (act === "modify" && type === "requirement") {
      h += field("requirement", "Requirement", "textarea", c.requirement) +
        '<div class="grid-2">' + field("priority", "Priority", LEVELS, c.priority) + field("risk_level", "Risk level", LEVELS, c.risk_level) + "</div>" +
        field("categories", "Categories (comma-separated)", "text", (c.categories || []).join(", ")) +
        field("acceptance_criteria", "Acceptance criteria (one per line)", "textarea", (c.acceptance_criteria || []).join("\n")) +
        field("business_justification", "Business justification", "text", c.business_justification) +
        field("dependencies", "Dependencies (comma-separated ids)", "text", (c.dependencies || []).join(", ")) +
        field("assumptions", "Assumptions (one per line)", "textarea", (c.assumptions || []).join("\n"));
    } else if (act === "modify" && type === "compliance") {
      h += field("status", "Status", ["satisfied", "gap", "not_applicable"], c.status) + field("note", "Required control", "text", c.required_control) +
        field("compliance_gap", "Gap (empty if none)", "text", c.compliance_gap || "") + field("checkpoints", "Checkpoints (one per line)", "textarea", (c.checkpoints || []).join("\n")) +
        field("retention_and_reporting", "Retention and reporting (one per line)", "textarea", (c.retention_and_reporting || []).join("\n"));
    } else if (act === "modify" && type === "sdlc") {
      h += field("recommended_model", "SDLC model", SDLC_MODELS, c.recommended_model) + '<p class="hint">Choosing another model records a human override; the scores are not changed.</p>';
    } else if (act === "regenerate") {
      h += '<p class="small muted">Re-runs this stage with your comment (one model call). Regenerations left: ' + esc(it.regenerations_left) + ".</p>";
    }
    h += field("comment", "Comment" + (act === "accept" ? " (optional)" : " (required)"), "textarea", "") +
      '<p class="hint">Acting as <strong>' + esc(S.role) + "</strong>. The server checks the role and the item state.</p>" +
      '<div class="btn-row" style="justify-content:flex-end"><button type="button" class="btn btn-ghost" data-action="close-dialog">Cancel</button>' +
      '<button type="submit" class="btn btn-primary" id="reviewSubmit">' + esc(act.charAt(0).toUpperCase() + act.slice(1)) + "</button></div></form>";
    d.innerHTML = h;
    d.showModal();
    $("reviewForm").addEventListener("submit", function (ev) {
      ev.preventDefault();
      var fd = new FormData(ev.target), body = { item_type: type, item_id: id, action: act, comment: String(fd.get("comment") || "").trim() };
      if (act !== "accept" && !body.comment) { $("f_comment").focus(); $("f_comment").setAttribute("aria-invalid", "true"); return; }
      if (act === "modify") {
        var changes = modifiedFields(type, c, fd);
        if (!Object.keys(changes).length) { body = null; window.alert("Nothing was changed."); return; }
        body.content = changes;
      }
      $("reviewSubmit").disabled = true; $("reviewSubmit").textContent = "Sending...";
      api("POST", "/runs/" + encodeURIComponent(S.result.run_id) + "/approvals", body).then(function (res) {
        d.close();
        addBanner("", "<strong>" + esc(type + " " + id) + "</strong> is now " + esc(res.status) + " (v" + esc(res.version) + ").", "review");
        if (res.masking && res.masking.total) maskingBanner(res.masking);
        return refreshAll();
      }).catch(function (e) { d.close(); showError(e, act + " " + id); refreshAll(); });
    });
  }
  function field(name, label, kind, value) {
    var idf = "f_" + name, v = value == null ? "" : value, inp;
    if (Array.isArray(kind)) inp = '<select id="' + idf + '" name="' + name + '">' + kind.map(function (o) { return "<option" + (o === v ? " selected" : "") + ">" + esc(o) + "</option>"; }).join("") + "</select>";
    else if (kind === "textarea") inp = '<textarea id="' + idf + '" name="' + name + '" rows="3" style="min-height:70px">' + esc(v) + "</textarea>";
    else inp = '<input type="text" id="' + idf + '" name="' + name + '" value="' + esc(v) + '">';
    return '<div class="field"><label for="' + idf + '">' + esc(label) + "</label>" + inp + "</div>";
  }
  function modifiedFields(type, c, fd) {
    var lines = function (s) { return String(s || "").split("\n").map(function (x) { return x.trim(); }).filter(Boolean); };
    var commas = function (s) { return String(s || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean); };
    var spec = type === "requirement" ? { requirement: "s", priority: "s", risk_level: "s", categories: commas, acceptance_criteria: lines, business_justification: "s", dependencies: commas, assumptions: lines }
      : type === "compliance" ? { status: "s", note: "s", compliance_gap: "n", checkpoints: lines, retention_and_reporting: lines }
      : { recommended_model: "s" };
    var current = type === "compliance" ? Object.assign({}, c, { note: c.required_control }) : c, out = {};
    Object.keys(spec).forEach(function (k) {
      var raw = fd.get(k), v = typeof spec[k] === "function" ? spec[k](raw) : spec[k] === "n" ? (String(raw || "").trim() || null) : String(raw || "").trim();
      if (JSON.stringify(v) !== JSON.stringify(current[k] == null && spec[k] === "n" ? null : current[k])) out[k] = v;
    });
    return out;
  }

  // ---------------------------------------------------------------- events
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-action]"); if (!b || b.disabled) return;
    var a = b.getAttribute("data-action");
    if (a === "tab") { S.tab = b.getAttribute("data-tab"); render(); window.scrollTo(0, 0); $("panel").focus({ preventScroll: true }); }
    else if (a === "art-tab") { S.artTab = b.getAttribute("data-tab"); renderPanel(); }
    else if (a === "dismiss") { var id = b.getAttribute("data-id"); S.banners = S.banners.filter(function (x) { return x.id !== id; }); renderBanners(); }
    else if (a === "run") runPipeline();
    else if (a === "load-sample") loadSample();
    else if (a === "add-answer") addAnswer();
    else if (a === "remove-answer") { S.input.answers.splice(+b.getAttribute("data-i"), 1); renderPanel(); }
    else if (a === "remove-doc") { S.input.documents.splice(+b.getAttribute("data-i"), 1); renderPanel(); }
    else if (a === "follow-ups") followUps();
    else if (a === "use-followup") {
      var q = S.followUps.questions[+b.getAttribute("data-i")];
      addAnswer({ role: q.role, question: q.question, answer: "", question_id: q.template_id || null, topic: q.topic || null });
    } else if (a === "use-template") {
      var t = S.templates.templates.filter(function (x) { return x.id === b.getAttribute("data-id"); })[0];
      if (t) addAnswer({ role: t.role, question: t.question, answer: "", question_id: t.id, topic: t.topic });
    } else if (a === "review") openReview(b.getAttribute("data-type"), b.getAttribute("data-id"), b.getAttribute("data-act"));
    else if (a === "close-dialog") $("dialog").close();
    else if (a === "srs-md") srs("markdown");
    else if (a === "srs-html") srs("html");
    else if (a === "freeze") freeze();
    else if (a === "refresh-run") { loadRun().then(refreshAll); }
    else if (a === "audit-this-run") { S.auditFilters.run_id = S.result.run_id; loadAudit(); }
  });
  document.addEventListener("input", function (ev) {
    var t = ev.target;
    if (t.id === "scenarioText") {
      S.input.scenario = t.value;
      var c = $("scenarioCount"); if (c) c.textContent = t.value.length + " / " + MAX_SCENARIO + " characters";
    } else if (t.hasAttribute && t.hasAttribute("data-answer")) {
      S.input.answers[+t.getAttribute("data-answer")][t.getAttribute("data-field")] = t.value;
    } else if (t.id === "fRun" || t.id === "fAction" || t.id === "fLimit") {
      S.auditFilters[{ fRun: "run_id", fAction: "action", fLimit: "limit" }[t.id]] = t.value.trim();
    }
  });
  document.addEventListener("change", function (ev) {
    var t = ev.target;
    if (t.id === "docInput") { readFiles(t.files); t.value = ""; }
    else if (t.id === "artefactsToggle") S.input.artefacts = t.checked;
    else if (t.id === "fRole") S.auditFilters.role = t.value;
    else if (t.hasAttribute && t.hasAttribute("data-answer")) {
      var ans = S.input.answers[+t.getAttribute("data-answer")]; ans[t.getAttribute("data-field")] = t.value;
    }
  });
  document.addEventListener("submit", function (ev) {
    if (ev.target.id === "auditForm") { ev.preventDefault(); loadAudit(); }
  });
  // Arrow keys move between tabs (WAI-ARIA tabs pattern).
  $("tabs").addEventListener("keydown", function (ev) {
    if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
    var t = tabsFor(), i = t.map(function (x) { return x.id; }).indexOf(S.tab);
    i = (i + (ev.key === "ArrowRight" ? 1 : t.length - 1)) % t.length;
    S.tab = t[i].id; render(); $("tab-" + S.tab).focus();
  });
  $("roleSelect").value = S.role;
  $("roleSelect").addEventListener("change", function (ev) {
    S.role = ev.target.value; localStorage.setItem("requora.role", S.role); S.audit = null; render();
  });
  $("newAnalysisBtn").addEventListener("click", newAnalysis);
  window.addEventListener("error", function (ev) { addBanner("error", "<strong>Page error:</strong> " + esc(ev.message)); });

  render();
  loadTemplates();
  var m = /^#run=([A-Za-z0-9_-]+)$/.exec(location.hash);   // reopen a stored run after a reload
  if (m) openRun(decodeURIComponent(m[1]));
})();
