/* Requora AI - inline SVG charts drawn by our own code (no charting library).
   Every function returns an SVG string; app.js inserts it into the page. */
(function () {
  "use strict";

  var C = {
    text: "#F2F2F5", muted: "#9A9AA4", line: "#26262B", panel: "#0B0B0D", panel2: "#151518",
    accent: "#B26CFF", accent2: "#C79CFF",
    heat: ["#1F1F26", "#3B2470", "#5B21B6", "#7C3AED", "#9333EA"]
  };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function r1(n) { return Math.round(n * 10) / 10; }
  function svgOpen(w, h, label) {
    return '<svg class="chart" viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="' + esc(label) +
      '" xmlns="http://www.w3.org/2000/svg">';
  }
  // Value 1..5 -> heat colour (0 or missing -> lowest).
  function heatFor(v) { return C.heat[Math.max(0, Math.min(4, (v || 1) - 1))]; }

  /* Radar: the project's 15 rated traits vs the recommended model's profile. */
  function radar(keys, labels, project, profile, profileName) {
    var w = 720, h = 480, cx = w / 2, cy = 238, R = 158, n = keys.length, out = [];
    out.push(svgOpen(w, h, "Radar chart: project trait ratings compared with the " + profileName + " profile"));
    function pt(i, v) {
      var a = -Math.PI / 2 + (2 * Math.PI * i) / n, rr = (R * v) / 5;
      return [cx + rr * Math.cos(a), cy + rr * Math.sin(a)];
    }
    for (var lvl = 1; lvl <= 5; lvl++) {
      var ring = keys.map(function (_, i) { return pt(i, lvl).map(r1).join(","); }).join(" ");
      out.push('<polygon points="' + ring + '" fill="none" stroke="' + C.line + '" stroke-width="1"/>');
    }
    keys.forEach(function (k, i) {
      var e = pt(i, 5), lp = pt(i, 5.95);
      out.push('<line x1="' + cx + '" y1="' + cy + '" x2="' + r1(e[0]) + '" y2="' + r1(e[1]) + '" stroke="' + C.line + '"/>');
      var anchor = Math.abs(lp[0] - cx) < 12 ? "middle" : lp[0] > cx ? "start" : "end";
      out.push('<text x="' + r1(lp[0]) + '" y="' + r1(lp[1] + 4) + '" font-size="12.5" text-anchor="' + anchor +
        '" fill="' + C.muted + '">' + esc(labels[k] || k) + "</text>");
    });
    for (var t = 1; t <= 5; t++) {
      var tp = pt(0, t);
      out.push('<text x="' + (cx + 4) + '" y="' + r1(tp[1] - 2) + '" font-size="9" fill="' + C.muted + '">' + t + "</text>");
    }
    function poly(vals, stroke, fill, dash) {
      var pts = keys.map(function (k, i) { return pt(i, vals[k] || 0).map(r1).join(","); }).join(" ");
      return '<polygon points="' + pts + '" fill="' + fill + '" stroke="' + stroke + '" stroke-width="2.2"' +
        (dash ? ' stroke-dasharray="6 4"' : "") + "/>";
    }
    if (profile) out.push(poly(profile, C.accent2, "none", true));
    out.push(poly(project, C.accent, "rgba(178,108,255,0.22)", false));
    keys.forEach(function (k, i) {
      var p = pt(i, project[k] || 0);
      out.push('<circle cx="' + r1(p[0]) + '" cy="' + r1(p[1]) + '" r="3.2" fill="' + C.accent + '"><title>' +
        esc((labels[k] || k) + ": project " + project[k] + (profile ? ", " + profileName + " " + profile[k] : "")) + "</title></circle>");
    });
    out.push("</svg>");
    return out.join("");
  }

  /* Heat matrix: rows = traits, columns = the project's ratings then each model's typical profile. */
  function heatMatrix(keys, labels, project, models, winner) {
    var labW = 250, cw = 70, rh = 26, top = 58, cols = [{ name: "This project", vals: project }].concat(
      models.map(function (m) { return { name: m.model, vals: m.profile }; }));
    var w = labW + cols.length * cw + 4, h = top + keys.length * rh + 6, out = [];
    out.push(svgOpen(w, h, "Heat matrix of trait values for the project and each SDLC model profile"));
    cols.forEach(function (c, j) {
      var x = labW + j * cw + cw / 2, win = c.name === winner;
      out.push('<text x="' + x + '" y="' + (top - 26) + '" font-size="11" text-anchor="middle" font-weight="' +
        (win || j === 0 ? 700 : 500) + '" fill="' + (win ? C.accent2 : j === 0 ? C.text : C.muted) + '">' + esc(c.name) + "</text>");
      if (win) out.push('<text x="' + x + '" y="' + (top - 10) + '" font-size="9.5" text-anchor="middle" fill="' + C.accent + '">recommended</text>');
    });
    keys.forEach(function (k, i) {
      var y = top + i * rh;
      out.push('<text x="' + (labW - 10) + '" y="' + (y + rh / 2 + 4) + '" font-size="11" text-anchor="end" fill="' + C.text + '">' +
        esc(labels[k] || k) + "</text>");
      cols.forEach(function (c, j) {
        var v = c.vals[k], x = labW + j * cw;
        out.push('<rect x="' + (x + 2) + '" y="' + (y + 2) + '" width="' + (cw - 4) + '" height="' + (rh - 4) +
          '" rx="3" fill="' + heatFor(v) + '"' + (j === 0 ? ' stroke="' + C.accent + '" stroke-width="1"' : "") + "><title>" +
          esc(c.name + " - " + (labels[k] || k) + ": " + v + "/5") + "</title></rect>");
        out.push('<text x="' + (x + cw / 2) + '" y="' + (y + rh / 2 + 4) + '" font-size="11.5" font-weight="600" text-anchor="middle" fill="#FFFFFF">' +
          esc(v) + "</text>");
      });
    });
    out.push("</svg>");
    return out.join("");
  }

  /* Ranked horizontal bars for all scored models (score is a 0-100 fit, not a probability). */
  function bars(scored, winner) {
    var rowH = 38, labW = 130, w = 640, barW = w - labW - 80, h = scored.length * rowH + 30, out = [];
    out.push(svgOpen(w, h, "Ranked fit scores of the seven SDLC models"));
    [0, 25, 50, 75, 100].forEach(function (g) {
      var x = labW + (barW * g) / 100;
      out.push('<line x1="' + x + '" y1="4" x2="' + x + '" y2="' + (h - 22) + '" stroke="' + C.line + '"/>');
      out.push('<text x="' + x + '" y="' + (h - 6) + '" font-size="10" text-anchor="middle" fill="' + C.muted + '">' + g + "</text>");
    });
    scored.forEach(function (m, i) {
      var y = 8 + i * rowH, bw = Math.max(2, (barW * m.score) / 100), win = m.model === winner;
      out.push('<text x="' + (labW - 10) + '" y="' + (y + 18) + '" font-size="14" text-anchor="end" font-weight="' + (win ? 700 : 500) +
        '" fill="' + (win ? C.text : C.muted) + '">' + esc("#" + m.rank + " " + m.model) + "</text>");
      out.push('<rect x="' + labW + '" y="' + (y + 4) + '" width="' + r1(bw) + '" height="20" rx="4" fill="' +
        (win ? C.accent : i === 1 ? C.heat[3] : C.heat[2]) + '"><title>' + esc(m.model + ": " + m.percent) + "</title></rect>");
      out.push('<text x="' + r1(labW + bw + 8) + '" y="' + (y + 19) + '" font-size="14" font-weight="700" fill="' + C.text + '">' +
        esc(m.percent) + "</text>");
    });
    out.push("</svg>");
    return out.join("");
  }

  /* 5x5 likelihood-by-impact grid; cell colour follows likelihood x impact, labels are risk numbers. */
  function riskGrid(risks) {
    var cell = 54, left = 62, top = 14, w = left + 5 * cell + 10, h = top + 5 * cell + 44, out = [];
    out.push(svgOpen(w, h, "Risk heat grid: likelihood by impact"));
    function band(s) { return s >= 20 ? 4 : s >= 15 ? 3 : s >= 10 ? 2 : s >= 5 ? 1 : 0; }
    for (var L = 5; L >= 1; L--) {
      for (var I = 1; I <= 5; I++) {
        var x = left + (I - 1) * cell, y = top + (5 - L) * cell;
        var here = risks.filter(function (r) { return r.likelihood === L && r.impact === I; });
        out.push('<rect x="' + (x + 1) + '" y="' + (y + 1) + '" width="' + (cell - 2) + '" height="' + (cell - 2) + '" rx="4" fill="' +
          C.heat[band(L * I)] + '"' + (here.length ? ' stroke="' + C.accent2 + '" stroke-width="1.5"' : "") + "><title>" +
          esc("Likelihood " + L + " x impact " + I + " = " + L * I + (here.length ? ": " + here.map(function (r) { return r.id; }).join(", ") : "")) +
          "</title></rect>");
        if (here.length) {
          out.push('<text x="' + (x + cell / 2) + '" y="' + (y + cell / 2 + 4) + '" font-size="11" font-weight="700" text-anchor="middle" fill="#FFFFFF">' +
            esc(here.map(function (r) { return r.id.replace("RISK-", "R"); }).join(" ")) + "</text>");
        }
      }
      out.push('<text x="' + (left - 8) + '" y="' + (top + (5 - L) * cell + cell / 2 + 4) + '" font-size="11" text-anchor="end" fill="' + C.muted + '">' + L + "</text>");
    }
    for (var i2 = 1; i2 <= 5; i2++) {
      out.push('<text x="' + (left + (i2 - 1) * cell + cell / 2) + '" y="' + (top + 5 * cell + 16) + '" font-size="11" text-anchor="middle" fill="' + C.muted + '">' + i2 + "</text>");
    }
    out.push('<text x="' + (left + 2.5 * cell) + '" y="' + (h - 6) + '" font-size="11" text-anchor="middle" fill="' + C.muted + '">Impact</text>');
    out.push('<text x="14" y="' + (top + 2.5 * cell) + '" font-size="11" text-anchor="middle" fill="' + C.muted +
      '" transform="rotate(-90 14 ' + (top + 2.5 * cell) + ')">Likelihood</text>');
    out.push("</svg>");
    return out.join("");
  }

  /* The /agents/pipeline steps. Dashed = an LLM writes this step's content; solid = deterministic code. */
  var PIPELINE = [
    { name: "Mask + filter input", kind: "code" },
    { name: "Requirements", kind: "llm" },
    { name: "KB retrieval", kind: "code", note: "vector search" },
    { name: "Compliance analysis", kind: "llm" },
    { name: "Compliance mapping", kind: "code" },
    { name: "Quality checks", kind: "llm", note: "rules + LLM" },
    { name: "Risk register", kind: "llm" },
    { name: "SDLC trait rating", kind: "llm" },
    { name: "SDLC scoring", kind: "code", note: "deterministic" },
    { name: "SDLC explanation", kind: "llm" },
    { name: "Workflow", kind: "llm", note: "template + LLM" },
    { name: "Artefacts", kind: "llm", note: "+ code matrices" },
    { name: "Store run + audit", kind: "code" },
    { name: "SRS document", kind: "code" }
  ];

  function pipeline() {
    var perRow = 7, bw = 132, bh = 50, gx = 20, gy = 38, w = perRow * bw + (perRow - 1) * gx + 8, rows = Math.ceil(PIPELINE.length / perRow);
    var h = rows * bh + (rows - 1) * gy + 8, out = [];
    out.push(svgOpen(w, h, "Pipeline diagram: dashed boxes are LLM steps, solid boxes are code steps"));
    out.push('<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="' + C.muted + '"/></marker></defs>');
    function pos(i) {
      var row = Math.floor(i / perRow), col = i % perRow;
      if (row % 2 === 1) col = perRow - 1 - col;           // snake layout
      return { x: 4 + col * (bw + gx), y: 4 + row * (bh + gy), row: row };
    }
    PIPELINE.forEach(function (s, i) {
      var p = pos(i);
      if (i > 0) {
        var q = pos(i - 1);
        if (q.row === p.row) {
          var ltr = p.x > q.x;
          out.push('<line x1="' + (ltr ? q.x + bw : q.x) + '" y1="' + (q.y + bh / 2) + '" x2="' + (ltr ? p.x - 2 : p.x + bw + 2) + '" y2="' + (p.y + bh / 2) +
            '" stroke="' + C.muted + '" marker-end="url(#arr)"/>');
        } else {
          out.push('<line x1="' + (q.x + bw / 2) + '" y1="' + (q.y + bh) + '" x2="' + (p.x + bw / 2) + '" y2="' + (p.y - 2) +
            '" stroke="' + C.muted + '" marker-end="url(#arr)"/>');
        }
      }
      var llm = s.kind === "llm";
      out.push('<rect x="' + p.x + '" y="' + p.y + '" width="' + bw + '" height="' + bh + '" rx="8" fill="' + C.panel + '" stroke="' +
        (llm ? C.accent : C.muted) + '" stroke-width="1.6"' + (llm ? ' stroke-dasharray="5 4"' : "") + "/>");
      out.push('<text x="' + (p.x + bw / 2) + '" y="' + (p.y + (s.note ? 22 : 29)) + '" font-size="11.5" font-weight="600" text-anchor="middle" fill="' + C.text + '">' +
        esc(s.name) + "</text>");
      if (s.note) out.push('<text x="' + (p.x + bw / 2) + '" y="' + (p.y + 37) + '" font-size="9.5" text-anchor="middle" fill="' + C.muted + '">' + esc(s.note) + "</text>");
    });
    out.push("</svg>");
    return out.join("");
  }

  window.Charts = { radar: radar, heatMatrix: heatMatrix, bars: bars, riskGrid: riskGrid, pipeline: pipeline,
                    heatFor: heatFor, PIPELINE: PIPELINE, colors: C };
})();
