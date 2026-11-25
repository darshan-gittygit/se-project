/* Requora AI - frontend logic
   State machine: idle -> requirements_ready -> compliance_ready -> sdlc_ready (or error at any point)
   Three real backend calls: POST /agents/requirements, POST /agents/compliance, POST /agents/sdlc
*/

let currentRequirements = null;
let currentScenario = '';  // scenario text the current requirements were generated from

function setStatus(mode, text) {
  const dot = document.getElementById('statusDot');
  const label = document.getElementById('statusText');
  dot.className = 'status-dot' + (mode === 'busy' ? ' busy' : mode === 'error' ? ' error' : '');
  label.textContent = text;
}

function setFlowStage(stage) {
  // stage: 1 = scenario input, 2 = requirements ready, 3 = compliance ready, 4 = sdlc ready
  for (let i = 1; i <= 4; i++) {
    document.getElementById('flowStep' + i).classList.toggle('active', i <= stage);
  }
  renderWorkflowBar(stage);
}

function renderWorkflowBar(stage) {
  const bar = document.getElementById('workflowBar');
  if (stage <= 1) { bar.classList.add('hidden'); return; }
  bar.classList.remove('hidden');
  const nodes = ['Scenario Input', 'Requirements Generated', 'Compliance & Risk', 'SDLC Analysis'];
  bar.innerHTML = nodes.map((label, idx) => {
    const step = idx + 1;
    const cls = step < stage ? 'done' : step === stage ? 'active' : '';
    const connector = idx > 0 ? '<div class="workflow-connector"></div>' : '';
    return `${connector}<div class="workflow-node ${cls}">${step < stage ? '&#10003;' : step}. ${label}</div>`;
  }).join('');
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str == null ? '' : str;
  return div.innerHTML;
}

async function generateRequirements() {
  const scenario = document.getElementById('scenario').value.trim();
  const resultsDiv = document.getElementById('results');
  const btn = document.getElementById('genBtn');

  if (scenario.length < 5) {
    resultsDiv.innerHTML = '<div class="panel error-panel"><p>Please enter a more detailed scenario.</p></div>';
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Generating...';
  setStatus('busy', 'Analyzing scenario...');
  resultsDiv.innerHTML = '<div class="panel loading-panel"><p>Agent 1 is extracting functional and non-functional requirements from your scenario...</p></div>';
  document.getElementById('hero').classList.add('compact');
  document.getElementById('resetBtn').classList.remove('hidden');

  try {
    const res = await fetch('/agents/requirements', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario })
    });
    const data = await res.json();

    if (!res.ok) {
      setStatus('error', 'Error');
      resultsDiv.innerHTML = `<div class="panel error-panel"><p>${escapeHtml(data.detail || 'Something went wrong.')}</p></div>`;
      return;
    }

    currentRequirements = data;
    currentScenario = scenario;
    setStatus('ready', 'System Ready');
    setFlowStage(2);
    renderRequirementsPanel(data);
  } catch (err) {
    setStatus('error', 'Error');
    resultsDiv.innerHTML = `<div class="panel error-panel"><p>Request failed: ${escapeHtml(err.message)}</p></div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Generate Requirements';
  }
}

function renderRequirementsPanel(data) {
  const fr = data.functional_requirements || [];
  const nfr = data.non_functional_requirements || [];
  const all = [...fr, ...nfr];
  const highCount = all.filter(r => r.priority === 'High').length;

  const cardHtml = (r, type) => `
    <div class="req-card">
      <div class="req-card-top">
        <span class="badge badge-id">${escapeHtml(r.id)}</span>
        <span class="badge ${type === 'FR' ? 'badge-fr' : 'badge-nfr'}">${type === 'FR' ? 'Functional' : 'Non-Functional'}</span>
        ${r.priority ? `<span class="badge badge-priority-${escapeHtml(r.priority)}">${escapeHtml(r.priority)} Priority</span>` : ''}
      </div>
      <div class="req-text">${escapeHtml(r.requirement)}</div>
    </div>
  `;

  const assumptionsHtml = (data.assumptions && data.assumptions.length)
    ? `<div class="assumptions-note"><strong>Assumptions made:</strong> ${data.assumptions.map(escapeHtml).join('; ')}</div>`
    : '';

  document.getElementById('results').innerHTML = `
    <div class="panel" id="requirementsPanel">
      <h2>Stage 1 — Requirements</h2>
      <p class="panel-subtitle" style="margin-bottom:18px;">${escapeHtml(data.domain_inferred)}</p>
      <div class="stats-row">
        <div class="stat-box"><div class="num">${all.length}</div><div class="label">Requirements</div></div>
        <div class="stat-box"><div class="num">${fr.length}</div><div class="label">Functional</div></div>
        <div class="stat-box"><div class="num">${nfr.length}</div><div class="label">Non-Functional</div></div>
        <div class="stat-box"><div class="num">${highCount}</div><div class="label">High Priority</div></div>
      </div>
      ${fr.map(r => cardHtml(r, 'FR')).join('')}
      ${nfr.map(r => cardHtml(r, 'NFR')).join('')}
      ${assumptionsHtml}
      <button class="btn-secondary" id="complianceBtn" onclick="runComplianceAnalysis()" style="margin-top:18px;">Run Compliance &amp; Risk Analysis &#8594;</button>
    </div>
  `;
}

async function runComplianceAnalysis() {
  if (!currentRequirements) return;
  const btn = document.getElementById('complianceBtn');
  btn.disabled = true;
  btn.textContent = 'Analyzing...';
  setStatus('busy', 'Retrieving compliance context...');

  const existingPanel = document.getElementById('compliancePanel');
  if (existingPanel) existingPanel.remove();

  const loadingDiv = document.createElement('div');
  loadingDiv.className = 'panel loading-panel';
  loadingDiv.id = 'complianceLoading';
  loadingDiv.innerHTML = '<p>Agent 3 is retrieving relevant regulatory context from the knowledge base and analyzing compliance risk...</p>';
  document.getElementById('results').appendChild(loadingDiv);

  try {
    const res = await fetch('/agents/compliance', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        domain: currentRequirements.domain_inferred,
        functional_requirements: currentRequirements.functional_requirements,
        non_functional_requirements: currentRequirements.non_functional_requirements
      })
    });
    const data = await res.json();
    document.getElementById('complianceLoading').remove();

    if (!res.ok) {
      setStatus('error', 'Error');
      const errDiv = document.createElement('div');
      errDiv.className = 'panel error-panel';
      errDiv.innerHTML = `<p>${escapeHtml(data.detail || 'Something went wrong.')}</p>`;
      document.getElementById('results').appendChild(errDiv);
      return;
    }

    setStatus('ready', 'System Ready');
    setFlowStage(3);
    renderCompliancePanel(data);
  } catch (err) {
    document.getElementById('complianceLoading').remove();
    setStatus('error', 'Error');
    const errDiv = document.createElement('div');
    errDiv.className = 'panel error-panel';
    errDiv.innerHTML = `<p>Request failed: ${escapeHtml(err.message)}</p>`;
    document.getElementById('results').appendChild(errDiv);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run Compliance & Risk Analysis \u2192';
  }
}

function renderCompliancePanel(data) {
  const considerationsHtml = (data.considerations || []).map(c => `
    <div class="consideration-card">
      <div class="req-card-top">
        ${(c.related_requirement_ids || []).map(id => `<span class="badge badge-id">${escapeHtml(id)}</span>`).join('')}
      </div>
      <div class="consideration-note">${escapeHtml(c.note)}</div>
      <span class="source-tag">${escapeHtml(c.source)}</span>
    </div>
  `).join('');

  const noConsiderations = (!data.considerations || data.considerations.length === 0);
  const kbNotice = (data.kb_relevance === 'not_relevant' || noConsiderations)
    ? `<div class="kb-notice">The curated knowledge base does not meaningfully cover this domain. No regulatory citations are being forced onto these requirements.</div>`
    : '';

  const sourcesUsed = (data.sources_used || []).map(s => `<span class="source-tag">${escapeHtml(s)}</span>`).join('');

  const panel = document.createElement('div');
  panel.className = 'panel';
  panel.id = 'compliancePanel';
  panel.innerHTML = `
    <h2>Stage 2 — Compliance &amp; Risk Analysis</h2>
    <div class="top-pick-card">
      <span class="badge badge-risk-${escapeHtml(data.risk_level)}" style="font-size:0.9rem; padding:7px 18px;">${escapeHtml(data.risk_level)} Risk</span>
    </div>
    <p style="color:var(--text-secondary); font-size:0.98rem; font-weight:500; line-height:1.6; margin-bottom:20px;">${escapeHtml(data.summary)}</p>
    ${considerationsHtml}
    ${kbNotice}
    ${sourcesUsed ? `<div class="assumptions-note"><strong>Knowledge base sources used:</strong><br>${sourcesUsed}</div>` : ''}
    <button class="btn-secondary" id="sdlcBtn" onclick="runSdlcAnalysis()" style="margin-top:18px;">Run SDLC Analysis &#8594;</button>
  `;
  document.getElementById('results').appendChild(panel);
}

async function runSdlcAnalysis() {
  if (!currentRequirements) return;
  const btn = document.getElementById('sdlcBtn');
  btn.disabled = true;
  btn.textContent = 'Analyzing...';
  setStatus('busy', 'Evaluating SDLC fit...');

  const existingDashboard = document.getElementById('sdlcPanel');
  if (existingDashboard) existingDashboard.remove();

  const loadingDiv = document.createElement('div');
  loadingDiv.className = 'panel loading-panel';
  loadingDiv.id = 'sdlcLoading';
  loadingDiv.innerHTML = '<p>Agent 2 is rating project traits and scoring candidate SDLC models...</p>';
  document.getElementById('results').appendChild(loadingDiv);

  try {
    const res = await fetch('/agents/sdlc', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        domain: currentRequirements.domain_inferred,
        functional_requirements: currentRequirements.functional_requirements,
        non_functional_requirements: currentRequirements.non_functional_requirements,
        scenario: currentScenario
      })
    });
    const data = await res.json();
    document.getElementById('sdlcLoading').remove();

    if (!res.ok) {
      setStatus('error', 'Error');
      const errDiv = document.createElement('div');
      errDiv.className = 'panel error-panel';
      errDiv.innerHTML = `<p>${escapeHtml(data.detail || 'Something went wrong.')}</p>`;
      document.getElementById('results').appendChild(errDiv);
      return;
    }

    setStatus('ready', 'System Ready');
    setFlowStage(4);
    renderSdlcPanel(data);
  } catch (err) {
    document.getElementById('sdlcLoading').remove();
    setStatus('error', 'Error');
    const errDiv = document.createElement('div');
    errDiv.className = 'panel error-panel';
    errDiv.innerHTML = `<p>Request failed: ${escapeHtml(err.message)}</p>`;
    document.getElementById('results').appendChild(errDiv);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run SDLC Analysis \u2192';
  }
}

function renderSdlcPanel(data) {
  const t = data.traits;
  const j = data.justification;
  const models = data.scored_models;
  const top = models[0];
  const maxScore = Math.max(...models.map(m => m.score));
  // Backend ties are equal unrounded scores; with the current weights those always
  // show as equal 1-decimal scores (checked in tests/test_scoring.py).
  const isTie = data.is_close_call && models.length > 1 && models[0].score === models[1].score;

  const traitRow = (label, value) => `
    <div class="trait-row">
      <div class="trait-label">${label}</div>
      <div class="trait-bar-bg"><div class="trait-bar-fill" style="width:${value * 20}%"></div></div>
      <div class="trait-value">${value}/5</div>
    </div>
  `;

  const compareRows = models.map(m => `
    <div class="compare-row">
      <div class="compare-label">${escapeHtml(m.model)}</div>
      <div class="compare-bar-bg"><div class="compare-bar-fill ${m.model === top.model ? 'top' : ''}" style="width:${(m.score / maxScore) * 100}%"></div></div>
      <div class="compare-value">${m.score}</div>
    </div>
  `).join('');

  const whyNotCards = models.filter(m => m.model !== top.model).map((m, idx) => `
    <div class="whynot-card">
      <button class="whynot-toggle" onclick="toggleSection('whynot-${idx}')">
        <span>Why not ${escapeHtml(m.model)}?</span>
        <span id="whynot-${idx}-arrow">&#9662;</span>
      </button>
      <div class="whynot-body hidden" id="whynot-${idx}">${escapeHtml(m.why_not)}</div>
    </div>
  `).join('');

  const traceSteps = [
    'Requirements analyzed',
    'Project traits extracted',
    'SDLC factors evaluated',
    'Models compared',
    'Recommendation generated'
  ];
  const traceHtml = traceSteps.map((label, i) => `
    <div class="trace-step">
      <div class="trace-line"></div>
      <div class="trace-dot">${i + 1}</div>
      <div class="trace-label">${label}</div>
    </div>
  `).join('');

  const allReqCount = (currentRequirements.functional_requirements.length + currentRequirements.non_functional_requirements.length);
  const timestamp = new Date().toLocaleString();
  const modelUsed = (data.meta && data.meta.model) ? data.meta.model : 'Gemini';

  const panel = document.createElement('div');
  panel.className = 'panel';
  panel.id = 'sdlcPanel';
  panel.innerHTML = `
    <h2>Stage 3 — SDLC Recommendation</h2>
    <div class="top-pick-card">
      <div class="top-pick-name">${escapeHtml(top.model)}</div>
      <div class="top-pick-score">Compatibility Score: <b>${top.score}</b>/100</div>
    </div>

    <div class="trait-grid">
      ${traitRow('Requirement Clarity', t.clarity)}
      ${traitRow('Requirement Volatility', t.volatility)}
      ${traitRow('Project Complexity', t.complexity)}
      ${traitRow('Project Criticality', t.criticality)}
      ${traitRow('Risk / Uncertainty', t.risk_uncertainty)}
      ${traitRow('Verification &amp; Validation', t.verification_validation)}
      ${traitRow('Stakeholder Feedback Need', t.stakeholder_feedback)}
      ${traitRow('Incremental Delivery Fit', t.incremental_suitability)}
      ${traitRow('Prototyping Need', t.prototyping_need)}
    </div>
    ${data.is_close_call ? `<div class="compare-caption" style="margin-bottom:18px;">${isTie
      ? `Tie: ${escapeHtml(top.model)} and ${escapeHtml(models[1].model)} have the same compatibility score (${top.score}/100). ${escapeHtml(top.model)} is recommended only by the deterministic tie-break (smaller worst single-trait gap, then smaller total gap, then alphabetical order) - treat this as a close call. See "Why not ${escapeHtml(models[1].model)}?" for details.`
      : `This is a close comparison with the runner-up model (${escapeHtml(models[1].model)}, ${models[1].score}/100) - not an overwhelmingly decisive gap.`}</div>` : ''}

    <button class="reasoning-toggle" onclick="toggleSection('reasoningBody')">
      <span>Why this recommendation?</span>
      <span id="reasoningBody-arrow">&#9662;</span>
    </button>
    <div class="reasoning-body hidden" id="reasoningBody">
      <div class="reasoning-section">
        <div class="reasoning-label">Requirement Characteristics</div>
        <p>${escapeHtml(j.requirement_characteristics)}</p>
      </div>
      <div class="reasoning-section">
        <div class="reasoning-label">Project Characteristics</div>
        <p>${escapeHtml(j.project_characteristics)}</p>
      </div>
      <div class="reasoning-section">
        <div class="reasoning-label">Engineering Justification</div>
        <p>${escapeHtml(j.engineering_justification)}</p>
      </div>
      <div class="reasoning-section">
        <div class="reasoning-label">Key Trade-offs</div>
        <p>${escapeHtml(j.key_tradeoffs)}</p>
      </div>
    </div>

    <h2 style="margin-top:28px;">Model Comparison</h2>
    <div class="compare-caption">Compatibility scores — deterministic fit against each SDLC model's trait profile, not an arbitrary AI ranking.</div>
    ${compareRows}

    <h2 style="margin-top:24px;">Alternative Models</h2>
    ${whyNotCards}

    <h2 style="margin-top:28px;">Analysis Trace</h2>
    <div class="trace-timeline">${traceHtml}</div>

    <div class="meta-row">
      <span>Requirements analyzed: ${allReqCount}</span>
      <span>Model: ${escapeHtml(modelUsed)}</span>
      <span>Generated: ${timestamp}</span>
    </div>
  `;
  document.getElementById('results').appendChild(panel);
}

function toggleSection(id) {
  const el = document.getElementById(id);
  const arrow = document.getElementById(id + '-arrow');
  el.classList.toggle('hidden');
  if (arrow) arrow.innerHTML = el.classList.contains('hidden') ? '&#9662;' : '&#9652;';
}

function resetApp() {
  currentRequirements = null;
  currentScenario = '';
  document.getElementById('scenario').value = '';
  document.getElementById('results').innerHTML = '';
  document.getElementById('hero').classList.remove('compact');
  document.getElementById('resetBtn').classList.add('hidden');
  document.getElementById('workflowBar').classList.add('hidden');
  setFlowStage(1);
  setStatus('ready', 'System Ready');
}
