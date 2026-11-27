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
    banners: [], busy: false,
    open: {}            // expanded table rows, by table and row key
  };
  if (ROLES.indexOf(S.role) < 0) S.role = "business analyst";

  // ---------------------------------------------------------------- helpers
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function $(id) { return document.getElementById(id); }
  function cap(s) { s = String(s == null ? "" : s).replace(/_/g, " "); return s.charAt(0).toUpperCase() + s.slice(1); }
  function list(arr, empty) {
    if (!arr || !arr.length) return empty == null ? '<span class="muted">-</span>' : empty;
    return "<ul>" + arr.map(function (x) { return "<li>" + esc(typeof x === "string" ? x : x.text) + "</li>"; }).join("") + "</ul>";
  }
  function chips(arr, cls) {
    if (!arr || !arr.length) return '<span class="muted">-</span>';
    return arr.map(function (c) { return '<span class="chip ' + (cls || "") + '">' + esc(c) + "</span>"; }).join("");
  }
  // Two badge colours only: violet for normal values, amber for values that need attention.
  function badge(text, warn) { return '<span class="badge' + (warn ? " warn" : "") + '">' + esc(text) + "</span>"; }
  function level(v, warnOn) {   // warnOn: the value that needs attention ("High" for risk, "Low" for confidence)
    if (!v) return '<span class="muted">-</span>';
    return badge(v, v === (warnOn || "High"));
  }
  var WARN_STATUSES = ["rejected", "gap", "modified", "pending", "open", "not_assessed", "no status"];
  function statusBadge(s) { return badge(cap(s || "-"), WARN_STATUSES.indexOf(s) >= 0); }
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
  function typeLabel(t) { return { requirement: "Requirement", compliance: "Compliance", sdlc: "SDLC", baseline: "Baseline" }[t] || cap(t); }
  function itemLabel(t, id) { return t === "sdlc" ? "SDLC selection" : t === "baseline" ? "Requirement baseline" : typeLabel(t) + " " + id; }
  function itemName(t, id) { return t === "sdlc" ? "SDLC selection" : id; }
  function stepText(s) { var t = String(s == null ? "" : s).trim(); return t.replace(/^\s*(?:step\s+)?\d+[.):]\s+/i, "").trim() || t; }
  function reviewCounts(cm) { var f = ((cm || {}).requirements || []).filter(function (e) { return e.needs_human_review; }); return { reqs: f.length, maps: f.reduce(function (n, e) { return n + (e.mappings || []).length; }, 0) }; }
  function reviewText(c) { return c.reqs + " requirement" + (c.reqs === 1 ? "" : "s") + " (" + c.maps + " mapping" + (c.maps === 1 ? "" : "s") + ")"; }
  function intro(title, sentence, aside) {
    return '<div class="tab-intro"><div><h2>' + esc(title) + "</h2><p>" + esc(sentence) + "</p></div>" + (aside || "") + "</div>";
  }
  function quote(text, src) {
    return '<div class="quote">&ldquo;' + esc(text) + "&rdquo;" + (src ? '<span class="src">' + esc(src) + "</span>" : "") + "</div>";
  }
  // Label/value pairs for an expanded row; empty values are left out. Values are HTML (escape before passing).
  function kv(pairs) {
    var rows = pairs.filter(function (p) { return p[1] != null && p[1] !== ""; });
    return '<dl class="kv">' + rows.map(function (p) { return "<dt>" + esc(p[0]) + "</dt><dd>" + p[1] + "</dd>"; }).join("") + "</dl>";
  }
  // Table with key columns only; a row with `detail` gets a toggle that opens a full-width row under it.
  function xtable(tid, headers, rows, empty) {
    if (!rows.length) return '<p class="muted">' + esc(empty || "Nothing to show.") + "</p>";
    return '<div class="table-wrap"><table class="xt"><thead><tr><th class="xcol"><span class="visually-hidden">Details</span></th>' +
      headers.map(function (x) { return "<th>" + esc(x) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      rows.map(function (r) {
        var k = tid + ":" + r.key, open = !!S.open[k], did = "d-" + k.replace(/[^A-Za-z0-9_-]/g, "_");
        return '<tr class="xrow' + (open ? " is-open" : "") + '"><td class="xcol">' + (r.detail ?
          '<button type="button" class="xbtn" data-action="toggle-row" data-key="' + esc(k) + '" data-target="' + did + '" aria-expanded="' + open +
          '" aria-controls="' + did + '" title="Show or hide details"><span aria-hidden="true">&#9656;</span><span class="visually-hidden">Details for ' +
          esc(r.label || r.key) + "</span></button>" : "") + "</td>" +
          r.cells.map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>" +
          (r.detail ? '<tr class="xdetail" id="' + did + '"' + (open ? "" : " hidden") + '><td></td><td colspan="' + headers.length + '">' + r.detail + "</td></tr>" : "");
      }).join("") + "</tbody></table></div>";
  }
  function table(headers, rows, empty) {
    if (!rows.length) return '<p class="muted">' + esc(empty || "None generated.") + "</p>";
    return '<div class="table-wrap"><table><thead><tr>' + headers.map(function (x) { return "<th>" + esc(x) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      rows.map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>"; }).join("") + "</tbody></table></div>";
  }
  function ids(a) { return esc((a || []).join(", ") || "-"); }
  function reviewButtons(type, id, acts, disabled, title) {
    return '<div class="act-row">' + acts.map(function (a) {
      return '<button type="button" class="btn btn-sm" data-action="review" data-type="' + type + '" data-id="' + esc(id) + '" data-act="' + a + '"' +
        (disabled ? " disabled" : "") + (title ? ' title="' + esc(title) + '"' : "") + ">" + cap(a) + "</button>";
    }).join("") + "</div>";
  }

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
        return "<li>" + esc(itemLabel(b.item_type, b.item_id) + " v" + b.version + " is " + b.status) + "</li>";
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

  // Tabs that only make sense once there is a result.
  var NEEDS_RESULT = ["overview", "requirements", "compliance", "sdlc", "artefacts", "approvals"];
  function enabled(id) { return NEEDS_RESULT.indexOf(id) < 0 || !!S.result; }

  function stageInfo(id) {
    var r = S.result;
    if (id === "scenario") {
      if (S.running) return { st: "running", sum: "Running, " + fmtTime(Date.now() - S.startedAt) };
      if (!r) return { st: "", sum: S.input.scenario ? S.input.scenario.length + " characters entered" : "Describe the project" };
      var na = (r.answers || []).length, nd = (r.documents || []).length;
      return { st: "done", sum: na + " answer" + (na === 1 ? "" : "s") + ", " + nd + " document" + (nd === 1 ? "" : "s") };
    }
    if (!r) return { st: "", sum: S.running ? "Waiting for the pipeline" : "Run an analysis first" };
    if (id === "requirements") {
      var q = (r.quality && r.quality.flags || []).length;
      return { st: q ? "attention" : "done", sum: allReqs().length + " requirements, " + q + " flag" + (q === 1 ? "" : "s") };
    }
    if (id === "compliance") {
      var rc = reviewCounts(r.compliance_mapping).reqs;
      var nr = r.risk_register ? r.risk_register.risks.length : 0;
      return { st: rc ? "attention" : "done", sum: rc + " need review, " + nr + " risks" };
    }
    if (id === "sdlc") {
      var s = r.sdlc_recommendation;
      if (!s) return { st: "", sum: "-" };
      var top = s.scored_models && s.scored_models[0];
      return { st: s.is_close_call ? "attention" : "done", sum: s.recommended_model + (top ? " " + top.percent : "") + (s.is_close_call ? ", close call" : "") };
    }
    if (id === "artefacts") {
      var a = r.artefacts || {};
      return { st: "done", sum: (a.user_stories || []).length + " stories, " + (a.use_cases || []).length + " use cases" };
    }
    if (id === "approvals") {
      if (S.sample) return { st: "", sum: "Not stored (sample)" };
      if (!S.run) return { st: "", sum: "Run summary not loaded" };
      var b = S.run.blockers.length;
      return { st: S.run.baseline.status === "frozen" ? "done" : "attention", sum: cap(S.run.baseline.status) + (b ? ", " + b + " blocking" : "") };
    }
    return { st: "", sum: "" };
  }
  function stageIcon(st) {
    if (st === "done") return '<span class="stage-icon done" aria-hidden="true">&#10003;</span>';
    if (st === "running") return '<span class="stage-icon running" aria-hidden="true"></span>';
    return '<span class="stage-icon dot' + (st === "attention" ? " attention" : "") + '" aria-hidden="true"></span>';
  }

  function renderSidebar() {
    $("stageList").innerHTML = STAGES.map(function (st) {
      var info = stageInfo(st.id), on = enabled(st.id);
      var state = { done: "done", running: "running", attention: "needs attention" }[info.st] || (on ? "not started" : "not available yet");
      return '<li><button type="button" class="stage-btn" data-action="tab" data-tab="' + st.id + '"' + (on ? "" : " disabled") +
        (S.tab === st.id ? ' aria-current="true"' : "") + ">" + stageIcon(info.st) +
        '<span class="stage-text"><span class="stage-name">' + esc(st.name) + '</span><span class="stage-sum">' + esc(info.sum) +
        '</span></span><span class="visually-hidden"> (' + state + ")</span></button></li>";
    }).join("");
    var r = S.result, html = "";
    if (r) {
      var v = r.versions || r.meta || {};
      html += (S.sample ? '<div><span class="badge warn">Recorded sample</span></div>' : "") +
        "<div>Run <code>" + esc(r.run_id || "-") + "</code></div>" +
        "<div>Model " + esc((v.models || []).join(", ") || "-") + "</div>" +
        "<div>App " + esc(v.app_version || "-") + ", KB " + esc(v.kb_version || "-") + "</div>" +
        (S.lastElapsed != null && !S.sample ? "<div>Took " + fmtTime(S.lastElapsed) + "</div>" : "");
    } else {
      html = "<div>No analysis yet. Enter a scenario, or load the recorded sample.</div>";
    }
    $("runInfo").innerHTML = html;
  }

  function tabsFor() {
    var t = [{ id: "overview", name: "Overview" }].concat(STAGES);
    if (AUDIT_ROLES.indexOf(S.role) >= 0) t.push({ id: "audit", name: "Audit log" });
    return t;
  }
  function renderTabs() {
    var t = tabsFor();
    if (!t.some(function (x) { return x.id === S.tab && enabled(x.id); })) S.tab = "scenario";
    $("tabs").innerHTML = t.map(function (x) {
      var on = enabled(x.id);
      return '<button type="button" class="tab" role="tab" id="tab-' + x.id + '" aria-controls="panel" aria-selected="' + (S.tab === x.id) +
        '" tabindex="' + (S.tab === x.id ? 0 : -1) + '" data-action="tab" data-tab="' + x.id + '"' +
        (on ? "" : ' disabled title="Run an analysis first"') + ">" + esc(x.name) + "</button>";
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
    var fn = { overview: viewOverview, scenario: viewScenario, requirements: viewRequirements, compliance: viewCompliance, sdlc: viewSdlc,
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
    return '<div class="card empty"><p>No ' + esc(what) + ' yet. Run an analysis first.</p>' +
      '<div class="btn-row" style="justify-content:center"><button type="button" class="btn" data-action="tab" data-tab="scenario">Go to Scenario</button>' +
      '<button type="button" class="btn btn-ghost" data-action="load-sample">Load recorded sample</button></div></div>';
  }

  // ---------------------------------------------------------------- view: overview
  var GENERATED = ["user_stories", "use_cases", "process_workflows", "data_requirements", "interface_requirements"];
  function closeCallNote(s) {
    var m = s.scored_models || [];
    if (!s.is_close_call || !m[1]) return "";
    return '<p class="close-note"><strong>Close call:</strong> ' + esc(m[0].model + " " + m[0].percent + " and " + m[1].model + " " + m[1].percent) +
      " are within 5 points. Both are reasonable; the four approvers decide.</p>";
  }
  function overrideNote(s) {
    var it = item("sdlc", "sdlc");
    var o = s.human_override || (it && it.content && it.content.human_override ? it.content.recommended_model : null);
    return o ? '<p class="close-note">Reviewers changed the selection to <strong>' + esc(o) + "</strong> (human override).</p>" : "";
  }
  function heroCard(s) {
    var models = s.scored_models || [];
    var top = models.filter(function (m) { return m.model === s.recommended_model; })[0] || models[0] || {};
    return '<div class="card hero-card"><div class="hero"><div><div class="hero-label">Recommended SDLC model</div><div class="hero-model">' + esc(s.recommended_model || "-") +
      '</div><p class="muted">Chosen by deterministic scoring against fixed model profiles; the LLM only rated the traits.</p></div>' +
      '<div class="hero-right"><div class="hero-pct">' + esc(top.percent || "") + '</div><div class="stat-label">Fit score</div></div></div>' +
      closeCallNote(s) + overrideNote(s) + "</div>";
  }
  function viewOverview() {
    if (!S.result) return needResult("analysis");
    var r = S.result, s = r.sdlc_recommendation || {}, a = r.artefacts || {};
    var high = ((r.risk_register || {}).risks || []).filter(function (x) { return x.level === "High"; }).length;
    var review = reviewCounts(r.compliance_mapping);
    var arts = GENERATED.reduce(function (n, k) { return n + (a[k] || []).length; }, 0);
    function tile(n, label, sub, tab) {
      return '<button type="button" class="tile" data-action="tab" data-tab="' + tab + '"><span class="stat">' + n + '</span><span class="tile-label">' + esc(label) +
        '</span><span class="tile-sub">' + esc(sub) + "</span></button>";
    }
    var baseline = S.sample ? "Not stored: this is the recorded sample, so nothing can be approved or frozen."
      : S.run ? cap(S.run.baseline.status) + (S.run.blockers.length ? ", with " + S.run.blockers.length + " mandatory item" + (S.run.blockers.length === 1 ? "" : "s") + " still to approve." : ".")
      : "The run summary could not be loaded.";
    return intro("Overview", "The result at a glance. Each number opens the tab with the details behind it.") +
      heroCard(s) +
      '<div class="tiles">' + tile(allReqs().length, "Requirements", "functional and non-functional", "requirements") +
      tile(high, "High risks", "in the risk register", "compliance") +
      tile(review.reqs, "Need review", reviewText(review) + " flagged for a person", "compliance") +
      tile(arts, "Artefacts", "stories, use cases, workflows, data, interfaces", "artefacts") + "</div>" +
      '<div class="card"><div class="card-head"><h3>Requirement baseline</h3>' + (S.sample ? badge("Sample", true) : S.run ? statusBadge(S.run.baseline.status) : "") + "</div>" +
      "<p>" + esc(baseline) + "</p>" +
      '<div class="btn-row"><button type="button" class="btn btn-primary" data-action="srs-md">Download SRS</button>' +
      '<button type="button" class="btn" data-action="srs-html">Open SRS</button>' +
      '<button type="button" class="btn btn-ghost" data-action="new-analysis">New analysis</button></div></div>';
  }

  // ---------------------------------------------------------------- view: scenario
  function roleOptions(selected) {
    var roles = (S.templates && S.templates.roles) || [];
    if (selected && roles.indexOf(selected) < 0) roles = [selected].concat(roles);
    return roles.map(function (r) { return '<option' + (r === selected ? " selected" : "") + ">" + esc(r) + "</option>"; }).join("");
  }
  function viewScenario() {
    var dis = S.running ? " disabled" : "";
    var h = intro("Scenario", "Describe the project, optionally add interview answers and documents, then run the analysis.",
      S.result ? "" : '<p class="tabs-note">Run an analysis first to open the other tabs.</p>');
    h += '<div class="card"><div class="card-head"><h3><label for="scenarioText">Project description</label></h3><span class="hint" id="scenarioCount">' +
      S.input.scenario.length + " / " + MAX_SCENARIO + " characters</span></div>" +
      '<textarea id="scenarioText" rows="7" maxlength="' + MAX_SCENARIO + '" placeholder="Example: A digital loan origination system for a mid-sized bank..."' + dis + ">" +
      esc(S.input.scenario) + '</textarea><p class="hint">Emails, phone numbers, card and ID numbers are replaced with placeholders on the server before any model call.</p></div>';

    // interview
    h += '<div class="card"><div class="card-head"><h3>Stakeholder interview (optional)</h3><div class="btn-row">' +
      '<button type="button" class="btn btn-sm" data-action="add-answer"' + dis + ">Add an answer</button>" +
      '<button type="button" class="btn btn-sm" data-action="follow-ups"' + (S.running || S.interviewBusy ? " disabled" : "") + ">" +
      (S.interviewBusy ? "Asking..." : "Suggest follow-up questions") + "</button></div></div>" +
      '<p class="hint">Requirements can quote these answers. Suggesting follow-up questions makes one model call.</p>';
    if (S.input.answers.length) {
      h += S.input.answers.map(function (a, i) {
        return '<div class="answer-row"><select aria-label="Stakeholder role for answer ' + (i + 1) + '" data-answer="' + i + '" data-field="role"' + dis + ">" +
          roleOptions(a.role) + '</select><div><input type="text" class="full" aria-label="Question ' + (i + 1) +
          '" data-answer="' + i + '" data-field="question" value="' + esc(a.question) + '"' + dis + '><textarea aria-label="Answer ' + (i + 1) +
          '" data-answer="' + i + '" data-field="answer" placeholder="Answer"' + dis + ">" + esc(a.answer) + "</textarea>" +
          (a.question_id ? '<span class="hint">Template ' + esc(a.question_id) + (a.topic ? ", " + esc(a.topic) : "") + "</span>" : "") +
          '</div><button type="button" class="btn btn-sm btn-ghost" data-action="remove-answer" data-i="' + i + '"' + dis + ' aria-label="Remove answer ' + (i + 1) + '">Remove</button></div>';
      }).join("");
    } else {
      h += '<p class="muted">No answers yet.</p>';
    }
    if (S.followUps) {
      h += "<h4>Suggested follow-ups</h4>";
      if (S.followUps.uncovered_topics && S.followUps.uncovered_topics.length) {
        h += '<p class="muted">Topics not yet covered: ' + esc(S.followUps.uncovered_topics.join(", ")) + "</p>";
      }
      h += (S.followUps.questions || []).map(function (q, i) {
        return '<div class="q-item"><div class="q-meta">' + esc(q.role) + ", " + esc(q.topic) + (q.template_id ? ", template " + esc(q.template_id) : "") +
          "</div><div>" + esc(q.question) + '</div><div class="hint">' + esc(q.reason) + '</div><button type="button" class="btn btn-sm" data-action="use-followup" data-i="' + i + '"' + dis + ">Answer this</button></div>";
      }).join("") || '<p class="muted">No follow-up questions returned.</p>';
    }
    if (S.templates) {
      var byRole = {};
      S.templates.templates.forEach(function (t) { (byRole[t.role] = byRole[t.role] || []).push(t); });
      h += '<details class="more"><summary>Question templates by stakeholder (' + S.templates.templates.length + ")</summary>" +
        Object.keys(byRole).map(function (role) {
          return "<h4>" + esc(role) + "</h4>" + byRole[role].map(function (t) {
            return '<div class="q-item"><div class="q-meta">' + esc(t.id) + ", " + esc(t.topic) + "</div><div>" + esc(t.question) +
              '</div><button type="button" class="btn btn-sm" data-action="use-template" data-id="' + esc(t.id) + '"' + dis + ">Answer this</button></div>";
          }).join("");
        }).join("") + "</details>";
    } else {
      h += '<p class="muted">Question templates are loading (or could not be loaded).</p>';
    }
    h += "</div>";

    // documents
    h += '<div class="card"><div class="card-head"><h3>Documents (optional)</h3><span class="hint">.txt, .md or .csv, up to ' + MAX_DOCS +
      " files of " + MAX_DOC_CHARS.toLocaleString() + ' characters</span></div><input type="file" id="docInput" accept=".txt,.md,.csv,text/plain,text/markdown,text/csv" multiple aria-label="Attach documents"' + dis + ">" +
      '<p class="hint">Files are read in your browser and sent as text; requirements can quote them by file name.</p>';
    if (S.input.documents.length) {
      h += '<ul class="doc-list">' + S.input.documents.map(function (d, i) {
        return "<li><span>" + esc(d.filename) + ' <span class="muted">(' + d.text.length.toLocaleString() + ' characters)</span></span><button type="button" class="btn btn-sm btn-ghost" data-action="remove-doc" data-i="' +
          i + '"' + dis + ">Remove</button></li>";
      }).join("") + "</ul>";
    }
    h += "</div>";

    // run
    h += '<div class="card"><div class="btn-row"><button type="button" class="btn btn-primary" data-action="run"' + dis + '>Run full analysis</button>' +
      '<label class="check"><input type="checkbox" id="artefactsToggle"' + (S.input.artefacts ? " checked" : "") + dis +
      "> Generate artefacts (one extra model call)</label>" +
      '<button type="button" class="btn btn-ghost" data-action="load-sample"' + dis + ">Load recorded sample</button></div>";
    if (S.running) {
      h += '<div class="running-box" role="status"><div class="spinner" aria-hidden="true"></div><div><div class="elapsed" id="elapsed">' +
        fmtTime(Date.now() - S.startedAt) + '</div><div class="muted">Running the whole pipeline as one request (' + (S.input.artefacts ? "about 8" : "about 7") +
        " model calls). The server reports no per-stage progress, so none is shown.</div></div></div>";
    }
    h += "</div>";

    h += '<details class="card more"><summary>How the pipeline works</summary><div class="legend"><span class="dashed" style="--c:#B26CFF">LLM step (dashed)</span>' +
      '<span style="--c:#9A9AA4">Code step (solid)</span></div>' + Charts.pipeline() +
      '<p class="caption">The SDLC model is chosen by deterministic scoring; the LLM only rates traits (with evidence) and writes the explanation.</p></details>';
    return h;
  }

  // ---------------------------------------------------------------- view: requirements
  function viewRequirements() {
    if (!S.result) return needResult("requirements");
    var r = S.result.requirements, q = S.result.quality || { flags: [], counts: {} };
    var flagsBy = {};
    (q.flags || []).forEach(function (f) { (f.requirement_ids || []).forEach(function (id) { (flagsBy[id] = flagsBy[id] || []).push(f); }); });
    var nf = (q.flags || []).length;
    var h = intro("Requirements", "Every requirement taken from the scenario, with its status. Open a row for its source quote, acceptance criteria and quality flags.",
      '<span class="chip accent">' + esc(r.domain_inferred) + "</span>");
    h += '<p class="summary-line">' + (r.functional_requirements || []).length + " functional and " + (r.non_functional_requirements || []).length +
      " non-functional requirements; " + nf + " quality flag" + (nf === 1 ? "" : "s") + "." +
      ((r.assumptions || []).length ? " Project assumptions: " + esc(r.assumptions.join("; ")) + "." : "") + "</p>";
    if (S.sample) h += '<p class="notice">Recorded sample: there is no stored run, so the review buttons are disabled.</p>';
    else if (S.role === "auditor") h += '<p class="notice">The auditor role is read-only; review buttons are disabled.</p>';

    var rows = allReqs().map(function (x) {
      var it = item("requirement", x.id), flags = flagsBy[x.id] || [];
      var disabled = !live() || S.role === "auditor" || !it || it.status === "superseded";
      var who = canReviewReq(x);
      var detail = kv([
        ["Source", esc(x.source_stakeholder || "-") + (x.source_text ? quote(x.source_text, x.source_document ? "from " + x.source_document : "from the scenario or an interview answer")
          : ' <span class="flag">No supporting quote (inferred)</span>')],
        ["Acceptance criteria", list(x.acceptance_criteria)],
        ["Why", esc(x.business_justification || "")],
        ["Risk level", level(x.risk_level)],
        ["Type", esc(x.type)],
        ["Depends on", (x.dependencies || []).length ? esc(x.dependencies.join(", ")) : ""],
        ["Assumes", (x.assumptions || []).length ? esc(x.assumptions.join("; ")) : ""],
        ["Regulations", (x.applicable_regulations || []).length ? esc(x.applicable_regulations.join(", ")) : ""],
        ["Quality flags", flags.length ? "<ul>" + flags.map(function (f) {
          return "<li>" + badge(cap(f.type), true) + " " + esc(f.reason) + (f.suggested_fix ? ' <span class="muted">Fix: ' + esc(f.suggested_fix) + "</span>" : "") + "</li>";
        }).join("") + "</ul>" : ""],
        ["Reviewer", esc(who.join(" or "))],
        ["More actions", reviewButtons("requirement", x.id, ["modify", "regenerate"], disabled, "Reviewer: " + who.join(" or "))],
        ["Versions", it && it.history.length > 1 ? esc(it.history.map(function (v) { return "v" + v.version + " " + v.status; }).join(", ")) : ""]
      ]);
      return { key: x.id, label: x.id, detail: detail, cells: [
        '<span class="id">' + esc(x.id) + "</span>" + (flags.length ? " " + badge(flags.length + " flag" + (flags.length === 1 ? "" : "s"), true) : ""),
        esc(x.requirement),
        esc((x.categories || []).join(", ")),
        esc(x.priority),
        level(x.confidence, "Low"),
        statusBadge(it ? it.status : x.approval_status) + (it ? ' <span class="ver">v' + it.version + "</span>" : ""),
        reviewButtons("requirement", x.id, ["accept", "reject"], disabled, "Reviewer: " + who.join(" or "))
      ] };
    });
    h += xtable("req", ["ID", "Statement", "Category", "Priority", "Confidence", "Status", "Actions"], rows);
    return h;
  }

  // ---------------------------------------------------------------- view: compliance and risk
  function viewCompliance() {
    if (!S.result) return needResult("compliance analysis");
    var c = S.result.compliance || {}, cm = S.result.compliance_mapping || { requirements: [] }, rr = S.result.risk_register;
    var note = String(cm.notice || "").replace(/^Not a legal determination\.\s*/, "");
    var h = intro("Compliance and risk", "How each requirement maps to the knowledge base, which findings a person must check, and the project's risk register.");
    h += '<p class="notice"><strong>Not a legal determination.</strong> ' + esc(note) + "</p>";
    h += '<div class="card"><div class="stats">' +
      '<div><div class="stat-label">Overall compliance risk</div><div>' + level(c.risk_level) + "</div></div>" +
      '<div><div class="stat-label">Knowledge-base coverage</div><div>' + badge(cap(c.kb_relevance || "-"), c.kb_relevance && c.kb_relevance !== "relevant") + "</div></div>" +
      '<div><div class="stat-label">Sources used</div><div>' + esc((c.sources_used || []).join(", ") || "-") + "</div></div>" +
      '<div><div class="stat-label">Need review</div><div class="stat">' + reviewCounts(cm).reqs + '</div><div class="muted">' + esc(reviewText(reviewCounts(cm))) + "</div></div></div>" +
      (c.summary ? '<p class="summary-line">' + esc(c.summary) + "</p>" : "") +
      (c.kb_relevance && c.kb_relevance !== "relevant" ? '<p class="notice">The knowledge base does not cover this domain well; findings are limited to what was retrieved.</p>' : "") + "</div>";

    h += '<h3 class="section-title">Compliance mapping</h3><p class="section-sub">One row per requirement and knowledge-base source. Open a row for the required control, evidence quote and review reasons.</p>';
    var rows = [];
    (cm.requirements || []).forEach(function (m) {
      var req = reqById(m.requirement_id);
      var reasons = (m.review_reasons || []).length ? "<ul>" + m.review_reasons.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>" : "";
      if (!m.mappings || !m.mappings.length) {
        rows.push({ key: m.requirement_id + ":none", label: m.requirement_id, detail: kv([["Requirement", esc(req ? req.requirement : "")], ["Review reasons", reasons]]),
          cells: ['<span class="id">' + esc(m.requirement_id) + "</span>", '<span class="muted">No knowledge-base match</span>', "-", "-",
                  m.needs_human_review ? badge("Review", true) : "-", ""] });
        return;
      }
      m.mappings.forEach(function (mp) {
        var it = item("compliance", m.requirement_id + "/" + mp.source), generic = mp.scope !== "domain-specific";
        var detail = kv([
          ["Requirement", esc(req ? req.requirement : "")],
          ["Regulation", esc(mp.regulation) + ' <span class="muted">(' + esc(generic ? "generic baseline" : "domain-specific") + ")</span>"],
          ["Jurisdiction", esc(mp.jurisdiction) + ", KB entry version " + esc(mp.version) + (mp.effective_date ? ", dated " + esc(mp.effective_date) : "")],
          ["Match", "chunk " + esc(mp.chunk_id) + ", distance " + esc(mp.distance)],
          ["Required control", esc(mp.required_control || "")],
          ["Gap", esc(mp.compliance_gap || "")],
          ["Evidence", mp.evidence ? quote(mp.evidence, (mp.evidence_verified ? "Verified against the retrieved text" : "Not found in the retrieved text") + " (paraphrased principles)") : ""],
          ["Checkpoints", (mp.checkpoints || []).length ? esc(mp.checkpoints.join("; ")) : ""],
          ["Retention and reporting", (mp.retention_and_reporting || []).length ? esc(mp.retention_and_reporting.join("; ")) : ""],
          ["Review reasons", reasons],
          ["Approval", it ? statusBadge(it.status) + ' <span class="ver">v' + it.version + "</span>" : ""],
          ["More actions", generic ? "" : reviewButtons("compliance", m.requirement_id + "/" + mp.source, ["modify"], !live() || S.role === "auditor" || !it, "Reviewer: compliance officer")]
        ]);
        var dis = !live() || S.role === "auditor" || !it;
        rows.push({ key: m.requirement_id + "/" + mp.source, label: m.requirement_id + " " + mp.source, detail: detail, cells: [
          '<span class="id">' + esc(m.requirement_id) + "</span>",
          esc(mp.source) + (generic ? ' <span class="muted">(generic)</span>' : ""),
          statusBadge(mp.status || "no status"),
          level(mp.confidence, "Low"),
          m.needs_human_review ? badge("Review", true) : "-",
          generic ? '<span class="muted">Context only</span>' : reviewButtons("compliance", m.requirement_id + "/" + mp.source, ["accept", "reject"], dis, "Reviewer: compliance officer")
        ] });
      });
    });
    h += xtable("map", ["Requirement", "Source", "Status", "Confidence", "Review", "Actions"], rows, "No requirements to map.");

    if ((c.dropped_considerations || []).length) {
      h += '<details class="card more"><summary>Dropped model claims (' + c.dropped_considerations.length + ")</summary>" +
        '<p class="muted">Claims the model made that could not be tied to retrieved text; they are not used above.</p>' +
        list(c.dropped_considerations.map(function (d) { return typeof d === "string" ? d : (d.source || "") + ": " + (d.note || "") + (d.reason ? " (" + d.reason + ")" : ""); })) + "</details>";
    }

    h += '<h3 class="section-title">Retrieved knowledge-base chunks</h3><p class="section-sub">Paragraphs whose distance passed the 0.62 threshold (squared L2; lower is closer).</p>' +
      xtable("chunk", ["Chunk", "Source", "Distance", "Used for this domain"], (c.retrieved_chunks || []).map(function (k) {
        return { key: k.id, label: k.id, detail: kv([["Scope", esc(k.scope)], ["Matched requirements", ids(k.matched_requirement_ids)]]),
          cells: ["<code>" + esc(k.id) + "</code>", esc(k.source), esc(k.distance), k.applies_to_domain ? "Yes" : '<span class="muted">No</span>'] };
      }), "No chunk passed the threshold.");

    if (rr) {
      var risks = (rr.risks || []).slice().sort(function (a, b) { return b.score - a.score || a.rank - b.rank; });
      var bl = (rr.summary && rr.summary.by_level) || {};
      h += '<h3 class="section-title">Risk register</h3><p class="section-sub">Likelihood times impact, each rated 1 to 5 against written anchors: ' + esc(rr.scoring || "") + ".</p>" +
        '<div class="card risk-top"><div class="risk-grid">' + Charts.riskGrid(risks) + '<p class="caption">Each cell is likelihood by impact; darker is a higher score. Labels are risk numbers.</p></div>' +
        '<div class="stats stats-3"><div><div class="stat-label">High</div><div class="stat">' + (bl.High || 0) + '</div></div><div><div class="stat-label">Medium</div><div class="stat">' +
        (bl.Medium || 0) + '</div></div><div><div class="stat-label">Low</div><div class="stat">' + (bl.Low || 0) + "</div></div></div></div>" +
        xtable("risk", ["Risk", "Description", "Category", "Likelihood", "Impact", "Score"], risks.map(function (x) {
          return { key: x.id, label: x.id, detail: kv([["Likelihood reason", esc(x.likelihood_reason)], ["Impact reason", esc(x.impact_reason)],
                                                          ["Mitigation", esc(x.mitigation)], ["Requirements", ids(x.linked_requirement_ids)]]),
            cells: ['<span class="id">' + esc(x.id) + "</span>", esc(x.description), esc(x.category), esc(x.likelihood), esc(x.impact),
                    "<strong>" + esc(x.score) + "</strong> " + level(x.level)] };
        }));
    }
    return h;
  }

  // ---------------------------------------------------------------- view: SDLC
  function viewSdlc() {
    if (!S.result || !S.result.sdlc_recommendation) return needResult("SDLC recommendation");
    var s = S.result.sdlc_recommendation, models = s.scored_models || [], top = models[0] || {};
    var winner = models.filter(function (m) { return m.model === s.recommended_model; })[0] || top;
    var it = item("sdlc", "sdlc");
    var h = intro("SDLC recommendation", "The model whose fixed profile best fits the project's 15 rated traits, with the reasons, the full ranking and the workflow.");
    h += heroCard(s);
    var j = s.justification || {};
    h += '<div class="card"><h3>Why this model</h3><div class="grid-2">' +
      [["Requirement characteristics", j.requirement_characteristics], ["Project characteristics", j.project_characteristics],
       ["Engineering justification", j.engineering_justification], ["Key trade-offs", j.key_tradeoffs]].map(function (p) {
        return "<div><h4>" + esc(p[0]) + "</h4><p>" + esc(p[1] || "-") + "</p></div>";
      }).join("") + "</div></div>";

    h += '<div class="card chart-card"><h3>Project traits against the ' + esc(winner.model) + ' profile</h3>' +
      '<div class="chart-box">' + Charts.radar(TRAIT_KEYS, TRAIT_LABELS, s.traits || {}, winner.profile, winner.model) + "</div>" +
      '<div class="legend"><span style="--c:#B26CFF">This project (rated 1-5)</span><span class="dashed" style="--c:#C79CFF">' + esc(winner.model) + " typical profile</span></div>" +
      '<p class="caption">Where the two shapes overlap, the project fits the model; gaps show the traits that pull towards other models.</p></div>';

    h += '<div class="card chart-card"><h3>Ranking of all seven models</h3><div class="chart-box narrow">' + Charts.bars(models, s.recommended_model) + "</div>" +
      '<p class="caption">Scores are a 0-100 weighted fit to each model\'s profile, not probabilities.</p>' +
      '<details class="more"><summary>Why not the others</summary><ul class="why-list">' +
      models.filter(function (m) { return m.why_not; }).map(function (m) {
        return "<li><strong>" + esc(m.model) + "</strong> (" + esc(m.percent) + "): " + esc(m.why_not) + "</li>";
      }).join("") + "</ul></details></div>";

    h += '<div class="card chart-card"><div class="card-head"><h3>Trait-by-model heat matrix</h3><div class="heat-legend">1 ' +
      Charts.colors.heat.map(function (c2, i) { return '<i style="background:' + c2 + '" title="' + (i + 1) + '"></i>'; }).join("") + " 5</div></div>" +
      '<div class="chart-box scroll-x">' + Charts.heatMatrix(TRAIT_KEYS, TRAIT_LABELS, s.traits || {}, models, s.recommended_model) + "</div>" +
      '<p class="caption">Each column is a model\'s typical rating for each trait; the outlined first column is this project. Darker means higher.</p></div>';

    var td = s.trait_details || {};
    h += '<h3 class="section-title">Trait ratings</h3><p class="section-sub">The LLM\'s rating for each trait. Open a row for its evidence and reason.</p>' +
      xtable("trait", ["Trait", "Score"], TRAIT_KEYS.map(function (k) {
        var d = td[k] || { score: (s.traits || {})[k] };
        return { key: k, label: TRAIT_LABELS[k], detail: kv([["Evidence", esc(d.evidence || "")], ["Reason", esc(d.reason || "")]]),
          cells: [esc(TRAIT_LABELS[k]), "<strong>" + esc(d.score) + "</strong> / 5"] };
      }));

    h += sdlcApprovalCard(s, it);
    h += workflowSection(S.result.sdlc_workflow);
    return h;
  }

  function sdlcApprovalCard(s, it) {
    var ar = s.approval_required || {};
    var accepted = it ? it.accepted_by || [] : ar.approved_by || [];
    var h = '<div class="card"><div class="card-head"><h3>SDLC approval</h3>' + statusBadge(it ? it.status : ar.status) + "</div>" +
      '<p class="muted">All four roles must accept the same version.</p>';
    h += '<div class="approver-grid">' + SDLC_APPROVERS.map(function (r) {
      var yes = accepted.indexOf(r) >= 0;
      return '<div class="approver' + (yes ? " yes" : "") + '"><div class="who">' + esc(cap(r)) + '</div><div class="' + (yes ? "" : "muted") + '">' +
        (yes ? "Accepted" : "Awaiting") + "</div></div>";
    }).join("") + "</div>";
    if (S.sample) h += '<p class="muted">Recorded sample: approvals are not stored, so all four show as awaiting.</p>';
    if (it) {
      var dis = !live() || SDLC_APPROVERS.indexOf(S.role) < 0;
      h += '<div class="btn-row"><span class="ver">v' + it.version + "</span>" + reviewButtons("sdlc", "sdlc", ["accept", "reject", "modify", "regenerate"], dis) +
        '<span class="muted">' + (SDLC_APPROVERS.indexOf(S.role) < 0 ? "Your role is not one of the four approvers." : "Acting as " + esc(S.role) + ".") + "</span></div>";
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
  function workflowSection(w) {
    if (!w) return "";
    var more = [["security_activities", "Security"], ["testing_requirements", "Testing"], ["compliance_checkpoints", "Compliance checkpoints"],
                ["entry_criteria", "Entry criteria"], ["exit_criteria", "Exit criteria"], ["traceability_requirements", "Traceability"]];
    return '<h3 class="section-title">' + esc(w.model) + ' workflow</h3><p class="section-sub">' + esc(w.added_items || 0) +
      " project-specific items were added to the fixed template (marked \"added\"). Open a phase for security, testing, compliance and entry and exit criteria.</p>" +
      xtable("wf", ["Phase", "Activities", "Roles", "Deliverables", "Approval gates"], (w.phases || []).map(function (p) {
        return { key: String(p.order), label: p.name, detail: kv(more.map(function (c) { return [c[1], wfCell(p[c[0]])]; })),
          cells: ["<strong>" + esc(p.order + ". " + p.name) + "</strong>", wfCell(p.activities), wfCell(p.responsible_roles), wfCell(p.deliverables), wfCell(p.approval_gates)] };
      }));
  }

  // ---------------------------------------------------------------- view: artefacts
  var ART_TEXT = {
    stories: "One or more stories per functional requirement, with acceptance criteria and the statements they come from.",
    usecases: "The main interactions, with actor, flows and conditions.",
    workflows: "End-to-end business processes, step by step.",
    data: "Data the system stores and the interfaces it needs.",
    rtm: "Built by code: each requirement linked to its source, compliance sources, risks and artefacts.",
    controls: "Built from the compliance mapping. Not a legal determination; the knowledge base is paraphrased principles, not law text.",
    assumptions: "Assumptions the requirements rely on, and which requirements depend on others.",
    issues: "Everything still open: quality flags, compliance reviews, pending approvals and unreviewed generated items."
  };
  function srcList(ss) {
    if (!ss || !ss.length) return "";
    return ss.map(function (s) {
      return s.statement ? quote(s.statement, s.requirement_id + ", " + s.stakeholder) :
        '<div class="muted">' + esc(s.requirement_id + ": no quote (inferred" + (s.assumptions && s.assumptions.length ? "; assumes " + s.assumptions.join("; ") : "") + ")") + "</div>";
    }).join("");
  }
  function viewArtefacts() {
    if (!S.result || !S.result.artefacts) return needResult("artefacts");
    var a = S.result.artefacts;
    var h = intro("Artefacts", "The SRS content generated from the requirements and the matrices built from it.",
      '<div class="btn-row"><button type="button" class="btn btn-primary" data-action="srs-md">Download SRS (Markdown)</button><button type="button" class="btn" data-action="srs-html">Open printable SRS</button></div>');
    h += '<p class="summary-line">' + esc(a.generation ? a.generation.note : "") + (S.sample ? " The sample SRS is rebuilt from this recorded result, with no model call." : " The SRS shows the current approval state.") + "</p>";
    h += '<div class="subtabs" role="tablist" aria-label="Artefact types">' + ART_TABS.map(function (t) {
      return '<button type="button" class="subtab" role="tab" aria-selected="' + (S.artTab === t.id) + '" data-action="art-tab" data-tab="' + t.id + '">' + esc(t.name) + "</button>";
    }).join("") + "</div>";
    var t = S.artTab;
    h += '<p class="section-sub">' + esc(ART_TEXT[t] || "") + "</p>";
    if (t === "stories") {
      h += (a.user_stories || []).map(function (u) {
        return '<div class="card"><div class="card-head"><span class="id">' + esc(u.id) + '</span><span class="muted">' + ids(u.requirement_ids) + "</span></div><p class=\"lead\">" + esc(u.story) +
          "</p><h4>Acceptance criteria</h4>" + list(u.acceptance_criteria) + '<details class="more"><summary>Source statements</summary>' + srcList(u.source_statements) + "</details></div>";
      }).join("") || '<p class="muted">None generated.</p>';
    } else if (t === "usecases") {
      h += (a.use_cases || []).map(function (u) {
        return '<div class="card"><div class="card-head"><h3>' + esc(u.id + " " + u.name) + '</h3><span class="muted">' + ids(u.requirement_ids) + "</span></div><p>Primary actor: <strong>" + esc(u.primary_actor) +
          '</strong></p><div class="grid-2"><div><h4>Main flow</h4><ol>' + (u.main_flow || []).map(function (s) { return "<li>" + esc(stepText(s)) + "</li>"; }).join("") +
          "</ol><h4>Alternative flows</h4>" + list(u.alternative_flows) + "</div><div><h4>Preconditions</h4>" + list(u.preconditions) + "<h4>Postconditions</h4>" + list(u.postconditions) + "</div></div>" +
          '<details class="more"><summary>Source statements</summary>' + srcList(u.source_statements) + "</details></div>";
      }).join("") || '<p class="muted">None generated.</p>';
    } else if (t === "workflows") {
      h += (a.process_workflows || []).map(function (w) {
        return '<div class="card"><div class="card-head"><h3>' + esc(w.id + " " + w.name) + '</h3><span class="muted">' + ids(w.requirement_ids) + "</span></div><ol>" +
          (w.steps || []).map(function (s) { return "<li><strong>" + esc(s.actor) + ":</strong> " + esc(s.action) + "</li>"; }).join("") + "</ol>" +
          '<details class="more"><summary>Source statements</summary>' + srcList(w.source_statements) + "</details></div>";
      }).join("") || '<p class="muted">None generated.</p>';
    } else if (t === "data") {
      h += '<h3 class="section-title">Data requirements</h3>' + xtable("data", ["ID", "Entity", "Description", "Requirements"], (a.data_requirements || []).map(function (d) {
        return { key: d.id, label: d.id, detail: kv([["Attributes", list(d.attributes)], ["Retention", esc(d.retention || "Not stated")], ["Sources", srcList(d.source_statements)]]),
          cells: ['<span class="id">' + esc(d.id) + "</span>", esc(d.entity), esc(d.description), ids(d.requirement_ids)] };
      }), "None generated.");
      h += '<h3 class="section-title">Interface requirements</h3>' + xtable("iface", ["ID", "Name", "Type", "Requirements"], (a.interface_requirements || []).map(function (d) {
        return { key: d.id, label: d.id, detail: kv([["Description", esc(d.description)], ["Sources", srcList(d.source_statements)]]),
          cells: ['<span class="id">' + esc(d.id) + "</span>", esc(d.name), esc(d.interface_type), ids(d.requirement_ids)] };
      }), "None generated.");
    } else if (t === "rtm") {
      h += xtable("rtm", ["Requirement", "Source", "Compliance", "Risks", "Status"], (a.traceability_matrix || []).map(function (r) {
        var cur = item("requirement", r.requirement_id);
        return { key: r.requirement_id, label: r.requirement_id, detail: kv([
            ["Requirement", esc(r.requirement)], ["Quote", r.source && r.source.statement ? quote(r.source.statement) : ""],
            ["User stories", ids(r.user_stories_ids)], ["Use cases", ids(r.use_cases_ids)], ["Process workflows", ids(r.process_workflows_ids)],
            ["Data", ids(r.data_requirements_ids)], ["Interfaces", ids(r.interface_requirements_ids)], ["Workflow items", list(r.workflow_items)]]),
          cells: ['<span class="id">' + esc(r.requirement_id) + "</span>", esc(r.source ? r.source.stakeholder : "-"),
                  esc((r.compliance_sources || []).map(function (c) { return c.source + " (" + String(c.status || "").replace(/_/g, " ") + ")"; }).join(", ") || "-"),
                  ids(r.risk_ids), statusBadge(cur ? cur.status : r.approval_status)] };
      }), "None generated.");
    } else if (t === "controls") {
      h += xtable("ctrl", ["Requirement", "Regulation", "Status", "Confidence"], (a.compliance_control_matrix || []).map(function (c, i) {
        return { key: c.requirement_id + "/" + (c.source || i), label: c.requirement_id, detail: kv([
            ["Scope", esc(c.scope)], ["Jurisdiction", esc(c.jurisdiction) + ", KB entry version " + esc(c.version)], ["Required control", esc(c.required_control || "")],
            ["Gap", esc(c.compliance_gap || "")], ["Evidence", c.evidence ? quote(c.evidence, c.evidence_verified ? "Verified" : "Not verified") : ""],
            ["Checkpoints and retention", list((c.checkpoints || []).concat(c.retention_and_reporting || []))],
            ["Review", c.needs_human_review ? badge("Needs review", true) : ""]]),
          cells: ['<span class="id">' + esc(c.requirement_id) + "</span>", esc(c.regulation), statusBadge(c.status), level(c.confidence, "Low")] };
      }), "None generated.");
    } else if (t === "assumptions") {
      var ar = a.assumptions_register || {};
      h += '<h3 class="section-title">Assumptions</h3>' + table(["ID", "Assumption", "Scope", "Requirements"], (ar.assumptions || []).map(function (x) {
        return ['<span class="id">' + esc(x.id) + "</span>", esc(x.text), esc(cap(x.scope)), ids(x.requirement_ids)];
      }));
      h += '<h3 class="section-title">Dependencies</h3>' + table(["Requirement", "Depends on"], (ar.dependencies || []).map(function (d) {
        return ['<span class="id">' + esc(d.requirement_id) + "</span>", ids(d.depends_on)];
      }));
    } else if (t === "issues") {
      h += table(["ID", "Type", "Description", "Requirements", "Origin"], (a.open_issues || []).map(function (x) {
        return ['<span class="id">' + esc(x.id) + "</span>", badge(cap(x.type), true), esc(x.description), ids(x.requirement_ids), esc(cap(x.origin))];
      }));
    }
    return h;
  }

  // ---------------------------------------------------------------- view: approvals
  function viewApprovals() {
    if (!S.result) return needResult("run to approve");
    var h = intro("Approvals", "The review status of every item and the requirement baseline. Freeze the baseline once every mandatory item is approved.");
    if (S.sample) {
      return h + '<div class="card"><p>This is the recorded sample. It has no stored run on this server, so there is nothing to accept, freeze or audit.</p>' +
        '<p class="muted">On a live run: requirements are reviewed by a business analyst or product owner (high-risk security requirements by the security officer), compliance interpretations ' +
        "by the compliance officer, and the SDLC selection by the project manager, architect, security officer and compliance officer together. The product owner or business analyst then freezes the baseline.</p></div>";
    }
    if (!S.run) return h + '<div class="card"><p>The run summary could not be loaded.</p><button type="button" class="btn" data-action="refresh-run">Try again</button></div>';
    var b = S.run.baseline, frozen = b.status === "frozen";
    h += '<div class="card"><div class="card-head"><h3>Requirement baseline</h3>' + statusBadge(b.status) + "</div>" +
      (frozen ? "<p>Frozen " + esc(b.frozen_at) + " by " + esc(b.frozen_by) + ". Any later change to an item reopens it.</p>"
        : "<p>Every requirement and every domain-specific compliance mapping must be approved first. The SDLC selection is approved separately and does not block the baseline.</p>") +
      '<div class="btn-row"><button type="button" class="btn btn-primary" data-action="freeze"' + (frozen ? " disabled" : "") + ">Freeze baseline</button>" +
      '<button type="button" class="btn btn-ghost" data-action="refresh-run">Refresh</button><span class="muted">Needs the product owner or business analyst role.</span></div></div>';
    h += '<h3 class="section-title">Blocking items (' + S.run.blockers.length + ")</h3>" + (S.run.blockers.length ? table(["Type", "Item", "Version", "Status"], S.run.blockers.map(function (x) {
      return [esc(typeLabel(x.item_type)), '<span class="id">' + esc(itemName(x.item_type, x.item_id)) + "</span>", "v" + esc(x.version), statusBadge(x.status)];
    })) : '<p class="muted">Nothing is blocking the baseline.</p>');
    h += '<h3 class="section-title">All approval items</h3>' + xtable("items", ["Type", "Item", "Version", "Status", "Mandatory"], S.run.items.map(function (x) {
      return { key: x.item_type + ":" + x.item_id, label: itemLabel(x.item_type, x.item_id), detail: kv([
          ["History", esc(x.history.map(function (v) { return "v" + v.version + " " + v.status; }).join(", "))],
          ["Regenerations left", esc(x.regenerations_left)],
          ["Accepted by", x.item_type === "sdlc" && x.accepted_by ? esc(x.accepted_by.join(", ") || "nobody yet") : ""]]),
        cells: [esc(typeLabel(x.item_type)), '<span class="id">' + esc(itemName(x.item_type, x.item_id)) + "</span>", "v" + esc(x.version), statusBadge(x.status), x.mandatory ? "Yes" : "No"] };
    }));
    h += '<h3 class="section-title">Decision history</h3>' + table(["When", "Role", "Action", "Item", "Version", "Comment"], (S.run.decisions || []).map(function (d) {
      return [esc(d.created_at), esc(cap(d.role)), esc(cap(d.action)), esc(itemLabel(d.item_type, d.item_id)), esc(d.version), esc(d.action === "freeze" ? "(snapshot of item versions)" : d.comment || "")];
    }), "No decisions yet.");
    return h;
  }

  // ---------------------------------------------------------------- view: audit
  function viewAudit() {
    var f = S.auditFilters;
    var h = intro("Audit log", "Every stage, review and download in an append-only, hash-chained log. Viewing it is itself logged.");
    h += '<div class="card"><form id="auditForm" class="filters"><div class="field"><label for="fRun">Run id</label><input type="text" id="fRun" value="' + esc(f.run_id) + '" placeholder="Any"></div>' +
      '<div class="field"><label for="fRole">Role</label><select id="fRole"><option value="">Any</option>' + ROLES.concat(["unspecified", "system"]).map(function (r) {
        return '<option value="' + esc(r) + '"' + (f.role === r ? " selected" : "") + ">" + esc(cap(r)) + "</option>";
      }).join("") + '</select></div><div class="field"><label for="fAction">Action</label><input type="text" id="fAction" value="' + esc(f.action) + '" placeholder="For example approval_accept"></div>' +
      '<div class="field"><label for="fLimit">Limit</label><input type="text" id="fLimit" value="' + esc(f.limit) + '" size="5" inputmode="numeric"></div>' +
      '<div class="field"><button type="submit" class="btn btn-primary">Load events</button></div>' +
      (S.result && S.result.run_id && !S.sample ? '<div class="field"><button type="button" class="btn btn-ghost" data-action="audit-this-run">This run only</button></div>' : "") + "</form></div>";
    if (!S.audit) return h + '<p class="muted">Load events to see the log and the chain check.</p>';
    var c = S.audit.chain;
    h += '<div class="banner ' + (c.ok ? "" : "error") + '"><div>' + (c.ok ? "<strong>Hash chain verified:</strong> " + (c.checked === 1 ? "the only event in the log matches its hash." : "all " + esc(c.checked) + " events in the log match their hashes.")
      : "<strong>Hash chain broken</strong> at event " + esc(c.first_bad_seq) + " after " + esc(c.checked) + " good events: " + esc(c.reason)) + "</div></div>";
    h += xtable("audit", ["Seq", "Time", "Role", "Action", "Stage"], S.audit.events.map(function (e) {
      var d = JSON.stringify(e.details, null, 2);
      return { key: String(e.seq), label: "event " + e.seq, detail: kv([
          ["Run", "<code>" + esc(e.run_id || "-") + "</code>"], ["Model", esc(e.model || "-")], ["Versions", esc(e.app_version + " / " + e.kb_version)],
          ["Details", '<pre class="mono">' + esc(d.slice(0, 6000)) + (d.length > 6000 ? "\n..." : "") + "</pre>"]]),
        cells: [esc(e.seq), esc(e.ts), esc(cap(e.role)), "<code>" + esc(e.action) + "</code>", esc(e.stage || "-")] };
    }), "No events match these filters.");
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
      S.open = {};
      return loadRun().then(function () { S.tab = "overview"; });
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
        S.open = {};
        if (S.tab === "scenario") S.tab = "overview";
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
      S.open = {}; S.tab = "overview"; render(); window.scrollTo(0, 0);
    }).catch(function (e) { showError(e, "loading the recorded sample"); });
  }
  function confirmReplace() { return window.confirm("Replace the current results? They stay stored on the server (if live) but leave this page."); }
  function newAnalysis() {
    if (S.running) return;
    if ((S.result || S.input.scenario) && !window.confirm("Start a new analysis? This clears the scenario and results on this page.")) return;
    S.result = null; S.run = null; S.sample = false; S.audit = null; S.followUps = null; S.lastElapsed = null;
    S.input = { scenario: "", answers: [], documents: [], artefacts: true };
    S.banners = []; S.open = {}; S.tab = "scenario"; render();
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
    var d = $("dialog"), c = it.content || {}, h = '<form method="dialog" id="reviewForm"><h3 id="dialogTitle">' + esc(act.charAt(0).toUpperCase() + act.slice(1)) + " " + esc(itemLabel(type, id)) +
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
        addBanner("", "<strong>" + esc(itemLabel(type, id)) + "</strong> is now " + esc(res.status) + " (v" + esc(res.version) + ").", "review");
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
    else if (a === "toggle-row") {
      var key = b.getAttribute("data-key"), row = $(b.getAttribute("data-target"));
      S.open[key] = !S.open[key];
      if (row) row.hidden = !S.open[key];
      b.setAttribute("aria-expanded", String(S.open[key]));
      b.closest("tr").classList.toggle("is-open", S.open[key]);
    }
    else if (a === "new-analysis") newAnalysis();
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
    var t = tabsFor().filter(function (x) { return enabled(x.id); }), i = t.map(function (x) { return x.id; }).indexOf(S.tab);
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
