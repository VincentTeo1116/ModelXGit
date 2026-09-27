/* RepoPilot · Model X onboarding frontend.
   Talks to the FastAPI backend (same origin by default, or the API URL set in Settings). */
"use strict";

// ------------------------------------------------------------------ constants
const SKILL_INFO = {
  "repo-clone": ["Clone & repo report", "Clones the repo and records its basic facts and risks"],
  "setup-dependencies": ["Setup plan", "How to install and run it, env variables and API keys"],
  "tech-stack-detection": ["Tech stack", "Languages, frameworks and tools, each with evidence"],
  "architecture-diagram": ["Architecture", "Layers, components and how they connect"],
  "git-history-secret-audit": ["Secret history audit", "Secrets committed in any commit, even if deleted later"],
  "secret-precommit-scanner": ["Secret scan", "Secrets in the current files and the pre-commit guard"],
  "codebase-qa": ["Codebase Q&A", "Role-based briefing and answers with file references"],
  "readme-generator": ["README generator", "Creates or completes the README from verified facts"],
};
const PIPELINE = ["repo-clone", "setup-dependencies", "tech-stack-detection", "architecture-diagram",
                  "git-history-secret-audit", "secret-precommit-scanner"];
const ROLES = ["Frontend", "Backend", "Full Stack", "Database", "QA / Testing", "DevOps", "AI / ML", "Other"];
const SUGGESTIONS = [
  "Give me my role briefing.",
  "What does this project do, in simple terms?",
  "Which files should I read first?",
  "How do I run it locally?",
  "What could break if I change the main entry file?",
];
const ACTIVE = new Set(["pending", "running", "queued"]);

// ------------------------------------------------------------------ state + settings
const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : v; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* storage blocked: keep defaults */ } },
};
const state = {
  apiBase: store.get("rp.apiBase", "").replace(/\/+$/, ""),
  pollMs: Number(store.get("rp.pollMs", "1500")) || 1500,
  health: null,
  jobs: [],
  timers: [],
  expanded: new Set(),
  openFile: null,
};

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const skillName = (n) => (SKILL_INFO[n] || [n])[0];
const skillDesc = (n) => (SKILL_INFO[n] || [, ""])[1];

function repoName(url) {
  const m = String(url || "").replace(/\.git$/, "").match(/([^/:]+)\/([^/]+)\/?$/);
  return m ? m[2] : url;
}
function repoOwner(url) {
  const m = String(url || "").replace(/\.git$/, "").match(/([^/:]+)\/([^/]+)\/?$/);
  return m ? m[1] : "";
}
function relTime(ts) {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function fmtDur(a, b) {
  if (!a) return "";
  const s = (b || Date.now() / 1000) - a;
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}
function fmtSecs(sec) {
  if (sec == null) return "—";
  const s = Math.round(sec);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}
function localBaseline() { return Number(store.get("rp.baseline", "")) || null; }
function tile(label, num, sub) {
  return `<div class="tile"><span class="lbl">${esc(label)}</span><span class="num">${esc(num)}</span><small>${esc(sub || "")}</small></div>`;
}
function compareHtml(onboardSec, serverBaseline) {
  const manual = localBaseline() || serverBaseline;
  if (!manual) return `<div class="compare hint">Add your team's measured time to onboard a repo by hand in <a href="#/settings">Settings</a> to compare. We never guess it.</div>`;
  if (!onboardSec) return `<div class="compare hint">Manual baseline: ${esc(manual)} min. The comparison appears when the pipeline finishes.</div>`;
  const auto = onboardSec / 60;
  const pct = Math.round(100 * (1 - auto / manual));
  return `<div class="compare">Manual baseline <strong>${esc(manual)} min</strong> → automated <strong>${esc(auto.toFixed(1))} min</strong>: <strong>${esc(pct)}% faster</strong>, ${esc((manual - auto).toFixed(1))} min saved per repo.</div>`;
}
// Bob ends answers with "You might ask next:" and a numbered list: turn it into chips.
function followUps(text) {
  const lines = String(text || "").split("\n");
  const start = lines.findIndex((l) => /you might ask next|suggested follow-?up|questions? you (could|might) ask/i.test(l));
  if (start < 0) return [];
  const qs = [];
  for (const l of lines.slice(start + 1)) {
    const m = l.match(/^\s*(?:\d+[.)]|[-*])\s+(.+?)\s*$/);
    if (m) qs.push(m[1].replace(/\*\*/g, "").replace(/`/g, "").trim());
    else if (qs.length && l.trim()) break;
    if (qs.length >= 3) break;
  }
  return qs.filter((q) => q.length > 3 && !/reply with a number/i.test(q));
}
// ---- readable onboarding files
const FILE_LABELS = {
  ONBOARDING_REPORT: "Onboarding report", README: "README", clone_report: "Clone report", setup: "Setup plan",
  setup_report: "Setup plan", tech_stack: "Tech stack", architecture: "Architecture",
  secrets_history: "Secret history audit", secrets_history_report: "Secret history audit",
  secrets_precommit: "Secret scan", metrics: "Impact metrics", CODEBASE_MAP: "Codebase map", readme_report: "README report",
};
function fileLabel(path) {
  const base = path.split("/").pop().replace(/\.(md|json|mmd)$/i, "");
  return FILE_LABELS[base] || base.replace(/[_-]+/g, " ").replace(/^./, (c) => c.toUpperCase());
}
const humanKey = (k) => String(k).replace(/[_-]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase());
const isScalar = (v) => v === null || v === undefined || typeof v !== "object";
function jrScalar(v) {
  if (v === null || v === undefined || v === "") return `<span class="jr-none">—</span>`;
  if (typeof v === "boolean") return v ? `<span class="jr-yes">✓ yes</span>` : `<span class="jr-no">✗ no</span>`;
  if (typeof v === "number") return `<span class="jr-num">${esc(v)}</span>`;
  const s = String(v);
  if (s.length > 140 || s.includes("\n")) return `<div class="md">${md(s)}</div>`;
  return /^https?:\/\//.test(s) ? `<a href="${esc(s)}" target="_blank" rel="noopener">${esc(s)}</a>` : esc(s);
}
function jrCell(v) {
  if (isScalar(v)) return jrScalar(v);
  if (Array.isArray(v)) return v.every(isScalar) ? v.map((x) => esc(x)).join(", ") || "—" : `${v.length} item${v.length === 1 ? "" : "s"}`;
  if (v.path) return esc(v.path) + (v.lines ? `:${esc(v.lines)}` : "");
  return Object.entries(v).map(([k, x]) => `${esc(humanKey(k))}: ${isScalar(x) ? esc(x) : "…"}`).join(" · ");
}
function jrValue(v, depth) {
  if (isScalar(v)) return jrScalar(v);
  if (Array.isArray(v)) {
    if (!v.length) return `<span class="jr-none">none</span>`;
    if (v.every(isScalar)) {
      return v.length <= 16 && v.every((x) => String(x).length < 60)
        ? `<div class="kv">${v.map((x) => `<span>${esc(x)}</span>`).join("")}</div>`
        : `<ul>${v.map((x) => `<li>${jrScalar(x)}</li>`).join("")}</ul>`;
    }
    if (v.every((x) => x && typeof x === "object" && !Array.isArray(x))) {
      const cols = [...new Set(v.flatMap((o) => Object.keys(o)))];
      const shown = cols.slice(0, 7);
      return `<div class="jr-table"><table><tr>${shown.map((c) => `<th>${esc(humanKey(c))}</th>`).join("")}</tr>${
        v.map((o) => `<tr>${shown.map((c) => `<td>${jrCell(o[c])}</td>`).join("")}</tr>`).join("")}</table></div>`
        + (cols.length > shown.length ? `<p class="qa-hint">Also: ${esc(cols.slice(7).map(humanKey).join(", "))} (see Raw JSON)</p>` : "");
    }
    return `<ul>${v.map((x) => `<li>${jrValue(x, depth + 1)}</li>`).join("")}</ul>`;
  }
  return jrObject(v, depth + 1);
}
function jrObject(o, depth) {
  const entries = Object.entries(o).filter(([k]) => k !== "stand_in");
  const simple = entries.filter(([, v]) => isScalar(v) || (Array.isArray(v) && v.every(isScalar) && v.length <= 16));
  const complex = entries.filter((e) => !simple.includes(e));
  const h = Math.min(depth + 2, 4);
  return `${simple.length ? `<dl class="jr-dl">${simple.map(([k, v]) => `<dt>${esc(humanKey(k))}</dt><dd>${jrValue(v, depth)}</dd>`).join("")}</dl>` : ""}${
    complex.map(([k, v]) => `<section class="jr-sec"><h${h}>${esc(humanKey(k))}</h${h}>${jrValue(v, depth)}</section>`).join("")}`;
}
function jsonReport(data, path) {
  const stand = data && data.stand_in ? `<p class="compare hint" style="margin-top:0">Stand-in output: the real Bob API wasn't used for this file.</p>` : "";
  return `<div class="jreport"><h2>${esc(fileLabel(path))}</h2>${stand}${jrValue(data, 0)}</div>`;
}

// One clean sentence from Bob's Markdown summary, for one-line subtitles.
function snippet(text, max = 160) {
  const plain = String(text || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/^\s*#{1,6}\s+.*$/gm, " ")
    .replace(/^\s*([-*]{3,}\s*$|>\s?)/gm, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[*_`]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (plain.length <= max) return plain;
  const cut = plain.slice(0, max);
  const end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("! "), cut.lastIndexOf("? "));
  return end > 60 ? cut.slice(0, end + 1) : cut.replace(/\s+\S*$/, "") + "…";
}
function statusPill(s) { return `<span class="status ${esc(s)}">${esc(s)}</span>`; }

async function api(path, opts = {}) {
  const init = { ...opts, headers: { Accept: "application/json", ...(opts.body ? { "Content-Type": "application/json" } : {}), ...(opts.headers || {}) } };
  let res;
  try {
    res = await fetch(state.apiBase + path, init);
  } catch {
    throw Object.assign(new Error("Can't reach the backend. Is it running? Check the API URL in Settings."), { status: 0 });
  }
  const type = res.headers.get("content-type") || "";
  const body = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = typeof body === "object" && body ? body.detail : body;
    const msg = Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : detail;
    throw Object.assign(new Error(msg || `Request failed (HTTP ${res.status})`), { status: res.status });
  }
  return body;
}

function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 4200);
}

function every(ms, fn) {
  const id = setInterval(fn, ms);
  state.timers.push(id);
  return id;
}
function clearTimers() { state.timers.forEach(clearInterval); state.timers = []; }

// Minimal, safe Markdown: escapes everything first, then adds formatting.
function md(src) {
  const blocks = [];
  let text = esc(src || "").replace(/```[^\n]*\n([\s\S]*?)```/g, (_, code) => {
    blocks.push(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });
  const inline = (s) => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const out = [];
  let list = null, para = [], table = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`); list = null; } };
  const flushTable = () => {
    if (!table.length) return;
    const rows = table.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r)).map((r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim())));
    out.push(`<table>${rows.map((r, i) => `<tr>${r.map((c) => i === 0 ? `<th>${c}</th>` : `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`);
    table = [];
  };
  for (const line of text.split("\n")) {
    let m;
    if (/^\u0000\d+\u0000$/.test(line.trim())) { flushPara(); flushList(); flushTable(); out.push(line.trim()); }
    else if ((m = line.match(/^&gt;\s?(.*)$/))) { flushPara(); flushList(); flushTable(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); }
    else if (/^\s*\|.*\|\s*$/.test(line)) { flushPara(); flushList(); table.push(line); }
    else if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { flushPara(); flushList(); flushTable(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); }
    else if ((m = line.match(/^\s*(?:[-*]|(\d+)\.)\s+(.*)$/))) {
      flushPara(); flushTable();
      const tag = m[1] ? "ol" : "ul";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(m[2]);
    }
    else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flushPara(); flushList(); flushTable(); out.push("<hr>"); }
    else if (!line.trim()) { flushPara(); flushList(); flushTable(); }
    else { flushList(); flushTable(); para.push(line.trim()); }
  }
  flushPara(); flushList(); flushTable();
  return out.join("\n").replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[Number(i)]);
}

let mermaidReady = null;
function loadMermaid() {
  if (!mermaidReady) {
    mermaidReady = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js";
      s.onload = () => { window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral" }); resolve(window.mermaid); };
      s.onerror = () => { mermaidReady = null; reject(new Error("mermaid unavailable")); };
      document.head.append(s);
    });
  }
  return mermaidReady;
}

// ------------------------------------------------------------------ health + jobs (sidebar/topbar)
async function refreshHealth() {
  const box = $("#bobStatus"), pill = $("#healthPill");
  let cls, title, sub, pillText;
  try {
    const h = await api("/api/health");
    state.health = h;
    const shell = h.bob_client === "shell";
    const local = !shell && /127\.0\.0\.1|localhost/.test(h.bob_url || "");
    if (!h.bob_configured) {
      cls = "warn"; pillText = "Backend up · Bob not ready";
      [title, sub] = shell && /not found/.test(h.bob_url) ? ["Bob Shell missing", "npm install -g bobshell"] : ["Bob key missing", "Add BOB_API_KEY to .env"];
    }
    else if (local) { cls = "warn"; title = "Bob stand-in"; sub = "Local test API, not real Bob"; pillText = "Backend up · Bob stand-in"; }
    else { cls = "ok"; title = "Bob connected"; sub = shell ? "via IBM Bob Shell" : h.bob_url.replace(/^https?:\/\//, "").split("/")[0]; pillText = "All systems ready"; }
    if (h.warnings && h.warnings.length) { cls = "warn"; sub = `${h.warnings.length} config warning(s)`; }
  } catch {
    state.health = null;
    cls = "bad"; title = "Backend offline"; sub = "Start it: uvicorn main:app"; pillText = "Backend offline";
  }
  box.className = `bob-status ${cls}`;
  $("#bobStatusTitle").textContent = title;
  $("#bobStatusSub").textContent = sub;
  pill.className = `health-pill ${cls}`;
  pill.querySelector("span").textContent = pillText;
}

async function refreshJobs() {
  try {
    state.jobs = await api("/api/jobs");
  } catch { /* shown via health */ }
  $("#navRepoCount").textContent = state.jobs.length;
  return state.jobs;
}

// ------------------------------------------------------------------ shared pieces
function heroHtml() {
  return `
  <section class="hero-copy">
    <div>
      <p class="eyebrow"><span class="eyebrow-dot"></span>MODEL X · AI DEVELOPER ONBOARDING</p>
      <h1>Onboard any repo.<br><em>Bob</em> does the reading.</h1>
      <p class="hero-description">Paste a repository link. Bob maps the tech stack and architecture, audits it for leaked secrets, plans the setup, and answers your questions with real file references.</p>
    </div>
    <div class="hero-decoration" aria-hidden="true"><span class="orb orb-one"></span><span class="orb orb-two"></span><span class="orbit-line"></span><span class="spark spark-one">✦</span><span class="spark spark-two">✦</span></div>
  </section>`;
}

function footerHtml() {
  return `<footer><span>RepoPilot <b>•</b> Model X onboarding <b>•</b> powered by IBM Bob</span><span><a href="${esc(state.apiBase)}/docs" target="_blank" rel="noopener">API docs</a> <b>•</b> v2.0</span></footer>`;
}

function repoCard(job) {
  const steps = PIPELINE.map((n) => (job.steps[n] || {}).status || "pending");
  const icon = { success: ["mint", "✓"], failed: ["coral", "!"], partial: ["coral", "◐"] }[job.status] || ["blue", "↻"];
  return `
  <a class="repo-card" href="#/job/${esc(job.id)}">
    <div class="repo-top"><span class="repo-icon ${icon[0]}">${icon[1]}</span>${statusPill(job.status)}</div>
    <h3>${esc(repoName(job.repo.url))}</h3>
    <div class="repo-url">${esc(repoOwner(job.repo.url))} · ${esc(job.repo.branch || "default branch")}</div>
    <div class="repo-meta"><span>${job.repo.commits != null ? esc(job.repo.commits) + " commits" : "cloning…"}</span><span>◷ ${esc(relTime(job.created_at))}</span></div>
    <div class="step-dots" title="${esc(PIPELINE.map((n, i) => `${skillName(n)}: ${steps[i]}`).join("\n"))}">${steps.map((s) => `<i class="${esc(s)}"></i>`).join("")}</div>
  </a>`;
}

function activityHtml(jobs, limit = 8) {
  const events = [];
  for (const j of jobs) {
    events.push({ t: j.created_at, job: j, icon: ["blue", "↓"], text: "Clone requested" });
    for (const [name, s] of Object.entries(j.steps)) {
      if (s.finished_at && s.status !== "skipped") {
        const ok = s.status === "success";
        events.push({ t: s.finished_at, job: j, icon: ok ? ["mint", "✓"] : ["coral", "!"],
                      text: `${skillName(name)} ${ok ? "finished" : "failed"}${!ok && s.error ? ": " + s.error : ""}` });
      }
    }
  }
  events.sort((a, b) => b.t - a.t);
  if (!events.length) return `<div class="empty">No activity yet. Clone a repository to start.</div>`;
  return `<div class="activity-list">${events.slice(0, limit).map((e) => `
    <a class="activity-item" href="#/job/${esc(e.job.id)}">
      <span class="activity-icon ${e.icon[0]}">${e.icon[1]}</span>
      <div><strong>${esc(repoName(e.job.repo.url))}</strong><p>${esc(e.text)}</p></div>
      <time>${esc(relTime(e.t))}</time>
    </a>`).join("")}</div>`;
}

// ------------------------------------------------------------------ views
const views = {};

views.home = async (view) => {
  view.innerHTML = `
    ${heroHtml()}
    <ol class="flow" aria-label="How it works">
      <li><span class="flow-n">1</span><strong>Paste a repo link</strong><small>you confirm before anything runs</small></li>
      <li><span class="flow-n">2</span><strong>Bob runs 5 skills in parallel</strong><small>setup, stack, architecture, 2 secret audits</small></li>
      <li><span class="flow-n">3</span><strong>Ask Bob, get a README</strong><small>role-based answers with file references</small></li>
      <li><span class="flow-n">4</span><strong>Continue in Bob IDE</strong><small>same skills + Q&amp;A mode in .bob/</small></li>
    </ol>

    <section class="card" id="clone">
      <div class="card-heading">
        <div><span class="step-pill">01</span><span class="heading-kicker">START HERE</span><h2>Clone a repository</h2></div>
        <span class="badge neutral" id="cloneBadge">◉ Public repos</span>
      </div>
      <p class="card-subtitle">Paste a public GitHub or GitLab link. You'll confirm before anything runs.</p>
      <form id="cloneForm" class="clone-form" novalidate>
        <label class="field-label" for="repoUrl">Repository URL</label>
        <div class="input-row" id="urlRow">
          <span class="link-icon">↗</span>
          <input id="repoUrl" type="text" inputmode="url" autocomplete="url" spellcheck="false" placeholder="https://github.com/owner/repository" aria-describedby="urlHint">
          <button type="submit" class="btn btn-primary"><span class="button-icon">↓</span>Clone repository<span class="button-arrow">→</span></button>
        </div>
        <div class="form-footer">
          <span id="urlHint">⌑ https://, ssh:// or git@ links · no passwords in the URL</span>
          <button type="button" class="options-button" id="optionsButton" aria-expanded="false" aria-controls="advancedOptions">Advanced options <span>⌄</span></button>
        </div>
        <div class="advanced-options" id="advancedOptions">
          <label for="branch">Branch<input class="text-input" id="branch" type="text" spellcheck="false" placeholder="default branch"></label>
          <label class="check-option" for="shallow"><input type="checkbox" id="shallow"><span>Shallow clone<small>Faster, but the secret history audit only sees the latest commit</small></span></label>
        </div>
        <div class="form-error" id="formError" hidden></div>
      </form>
      <div class="confirm-panel" id="confirmPanel" hidden></div>
    </section>

    <section class="section-head"><div><p class="eyebrow">MEASURED BY THE BACKEND</p><h2>Impact so far</h2></div></section>
    <div id="impactStrip"></div>

    <section class="section-head"><div><p class="eyebrow">YOUR PROJECTS</p><h2>Recent repositories</h2></div><a href="#/repos" class="view-all">View all <span>→</span></a></section>
    <div id="recent"></div>

    <section class="section-head"><div><p class="eyebrow">WHAT'S HAPPENING</p><h2>Latest activity</h2></div></section>
    <div id="activity"></div>
    ${footerHtml()}`;

  const form = $("#cloneForm"), input = $("#repoUrl"), row = $("#urlRow"), err = $("#formError"), panel = $("#confirmPanel");
  $("#optionsButton").addEventListener("click", (e) => {
    const open = $("#advancedOptions").classList.toggle("visible");
    e.currentTarget.classList.toggle("open", open);
    e.currentTarget.setAttribute("aria-expanded", open);
  });
  input.addEventListener("input", () => { row.classList.remove("invalid"); err.hidden = true; });

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const url = input.value.trim();
    const valid = /^(https:\/\/[\w.-]+(:\d+)?\/[\w.~%+-]+\/[\w.~%+/-]+|git@[\w.-]+:[\w.~/-]+|ssh:\/\/[\w.-]+@[\w.-]+(:\d+)?\/[\w.~/-]+)$/.test(url) && !/^https:\/\/[^/]*@/.test(url);
    if (!valid) {
      row.classList.remove("invalid"); void row.offsetWidth; row.classList.add("invalid");
      err.textContent = "Enter a repository link like https://github.com/owner/repository";
      err.hidden = false; input.focus();
      return;
    }
    const branch = $("#branch").value.trim();
    const shallow = $("#shallow").checked;
    const autoSkills = PIPELINE.slice(1);
    panel.innerHTML = `
      <h3>Clone ${esc(repoName(url))} and run the pipeline?</h3>
      <p><code>${esc(url)}</code>${branch ? ` · branch <code>${esc(branch)}</code>` : " · default branch"}${shallow ? " · shallow clone" : " · full history"}</p>
      <ul>
        <li>The backend clones the repository, then Bob writes the clone report.</li>
        <li>These run automatically with Bob: ${autoSkills.map((s) => `<strong>${esc(skillName(s))}</strong>`).join(", ")}.</li>
        <li>Bob reads the cloned code (IBM Bob). It can't run commands and may only write to <code>onboarding/</code> and <code>README.md</code>; anything else it changes is undone. Secret files such as keys and credentials are hidden from Bob.</li>
      </ul>
      <div class="confirm-actions">
        <button class="btn btn-primary" id="confirmClone" type="button">Clone and run skills</button>
        <button class="btn btn-ghost" id="cancelClone" type="button">Cancel</button>
      </div>`;
    panel.hidden = false;
    $("#confirmClone").focus();
    $("#cancelClone").onclick = () => { panel.hidden = true; input.focus(); };
    $("#confirmClone").onclick = async (e) => {
      e.currentTarget.disabled = true;
      e.currentTarget.textContent = "Starting…";
      try {
        const res = await api("/api/repos/clone", { method: "POST", body: JSON.stringify({ repo_url: url, branch: branch || null, depth: shallow ? 1 : null, confirm: true }) });
        toast(`Cloning ${repoName(url)}…`, "ok");
        location.hash = `#/job/${res.job_id}`;
      } catch (ex) {
        panel.hidden = true;
        err.textContent = ex.message;
        err.hidden = false;
      }
    };
  });

  const renderLists = async () => {
    const jobs = await refreshJobs();
    $("#recent").innerHTML = jobs.length
      ? `<div class="repo-grid">${jobs.slice(0, 3).map(repoCard).join("")}</div>`
      : `<div class="empty">No repositories yet. Paste a link above and Bob will take it from there.</div>`;
    $("#activity").innerHTML = activityHtml(jobs);
    try {
      const m = await api("/api/metrics");
      $("#impactStrip").innerHTML = m.repos_onboarded
        ? `<div class="impact-grid five">
            ${tile("Repos onboarded", m.repos_onboarded, "full pipeline finished")}
            ${tile("Avg time to onboard", fmtSecs(m.avg_time_to_onboard_seconds), "fastest " + fmtSecs(m.fastest_seconds))}
            ${tile("Files produced", m.files_produced, "onboarding/ + README")}
            ${tile("Secret findings", m.secret_findings, "masked, history + current")}
            ${tile("Bob tool calls", m.bob_tool_calls, m.bob_runs + " Bob runs")}
          </div>${compareHtml(m.avg_time_to_onboard_seconds, m.manual_baseline_minutes)}`
        : `<div class="empty">Numbers appear here after the first onboarding finishes.</div>`;
    } catch { /* shown via health */ }
  };
  await renderLists();
  every(3000, () => { if (document.visibilityState === "visible") renderLists(); });
  if (state.focusClone) { state.focusClone = false; input.focus(); }
};

views.repos = async (view) => {
  const render = async () => {
    const jobs = await refreshJobs();
    view.innerHTML = `
      <section class="section-head" style="margin-top:0"><div><p class="eyebrow">YOUR PROJECTS</p><h2>Repositories</h2></div><a class="btn btn-primary btn-sm" href="#/">＋ Clone a repository</a></section>
      ${jobs.length ? `<div class="repo-grid">${jobs.map(repoCard).join("")}</div>` : `<div class="empty">No repositories yet. Jobs are kept while the backend runs; restarting it clears the list.</div>`}
      <section class="section-head"><div><p class="eyebrow">WHAT'S HAPPENING</p><h2>All activity</h2></div></section>
      ${activityHtml(jobs, 40)}
      ${footerHtml()}`;
  };
  await render();
  every(4000, render);
};

views.skills = async (view) => {
  view.innerHTML = `<div class="empty">Loading skills…</div>`;
  let data;
  try { data = await api("/api/skills"); } catch (e) { view.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const card = (s) => `
    <div class="skill-card">
      <h4>${esc(s.name)}</h4>
      <p title="${esc(s.description)}">${esc(skillDesc(s.name) || s.description)}</p>
      <div class="kv">
        ${s.parallel_group ? `<span class="accent">group: ${esc(s.parallel_group)}</span>` : ""}
        ${s.depends_on.length ? `<span>waits for: ${esc(s.depends_on.join(", "))}</span>` : ""}
        ${s.local_tools.length ? `<span>runs: ${esc(s.local_tools.map((t) => t.split(" ")[0].replace("scripts/", "")).join(", "))}</span>` : ""}
        <span>${esc(s.produces.length)} output file${s.produces.length === 1 ? "" : "s"}</span>
      </div>
    </div>`;
  view.innerHTML = `
    <section class="hero-copy" style="min-height:0;margin-bottom:28px">
      <div><p class="eyebrow"><span class="eyebrow-dot"></span>THE PIPELINE</p><h1>Eight skills.<br><em>One</em> onboarding.</h1>
      <p class="hero-description">Each skill is a SKILL.md file. Its frontmatter decides when it runs and what it waits for. This page reads them live from the backend.</p></div>
    </section>
    <div class="pipeline-flow">
      <div class="flow-col"><h3>1 · When you confirm a clone</h3><p>The backend clones, then Bob writes the report.</p>${data.entry.map(card).join("")}</div>
      <div class="flow-col"><h3>2 · Automatically after the clone</h3><p>Run in parallel; a skill waits only for its dependencies.</p>${data.auto.map(card).join("")}</div>
      <div class="flow-col"><h3>3 · When you pick them</h3><p>From the repository page: ask questions, generate the README.</p>${data.manual.map(card).join("")}</div>
    </div>
    ${data.warnings.length ? `<ul class="warn-list">${data.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
    ${footerHtml()}`;
};

views.settings = async (view) => {
  view.innerHTML = `
    <section class="section-head" style="margin-top:0"><div><p class="eyebrow">CONFIGURATION</p><h2>Settings</h2></div></section>
    <div class="settings-grid">
      <div class="card">
        <div class="card-title-row"><h2 class="card-title">This browser</h2></div>
        <label class="field" for="apiBase">Backend URL<input class="text-input" id="apiBase" type="text" spellcheck="false" placeholder="same address as this page"><small>Leave empty when the backend serves this page. Set it (e.g. http://127.0.0.1:8000) when the page is opened from somewhere else.</small></label>
        <label class="field" for="pollMs">Live update speed
          <select class="text-input" id="pollMs"><option value="1000">Every second</option><option value="1500">Every 1.5 seconds</option><option value="3000">Every 3 seconds</option><option value="5000">Every 5 seconds</option></select></label>
        <label class="field" for="baseline">Manual onboarding time (minutes)<input class="text-input" id="baseline" type="number" min="1" step="1" placeholder="your team's own measurement"><small>How long it takes your team to onboard a repo by hand. Used only for the "faster" comparison. Leave it empty rather than guess.</small></label>
        <div class="confirm-actions"><button class="btn btn-primary" id="saveSettings" type="button">Save</button><button class="btn btn-ghost" id="testConn" type="button">Test connection</button></div>
      </div>
      <div class="card">
        <div class="card-title-row"><h2 class="card-title">Backend and Bob</h2><span id="healthBadge"></span></div>
        <div id="healthFacts"><div class="empty">Checking…</div></div>
      </div>
    </div>
    <div class="card" style="margin-top:18px">
      <div class="card-title-row"><h2 class="card-title">Connect the real Bob API</h2></div>
      <p class="card-note">The backend runs each skill with IBM Bob Shell (<code>npm install -g bobshell</code>). Create <code>.env</code> next to <code>main.py</code> and restart the backend. Create the key at bob.ibm.com → API keys; set BOB_ACCEPT_LICENSE only after reading the license (<code>bob --show-license</code>).</p>
      <div class="md"><pre><code>BOB_CLIENT=shell
BOB_API_KEY=your key
BOB_ACCEPT_LICENSE=true</code></pre></div>
    </div>
    ${footerHtml()}`;
  $("#apiBase").value = state.apiBase;
  $("#pollMs").value = String(state.pollMs);
  $("#baseline").value = store.get("rp.baseline", "");
  const showHealth = async () => {
    await refreshHealth();
    const h = state.health;
    if (!h) { $("#healthFacts").innerHTML = `<div class="empty">Backend offline at ${esc(state.apiBase || location.origin)}</div>`; $("#healthBadge").innerHTML = statusPill("failed"); return; }
    $("#healthBadge").innerHTML = `<span class="badge ${$("#bobStatus").classList.contains("ok") ? "ok" : "warn"}">${esc($("#bobStatusTitle").textContent)}</span>`;
    $("#healthFacts").innerHTML = `
      <dl class="facts-list">
        <dt>Bob key</dt><dd>${h.bob_configured ? "set" : "missing"}</dd>
        <dt>Bob connection</dt><dd>${esc(h.bob_client === "shell" ? "IBM Bob Shell (headless)" : "HTTP")} · ${esc(h.bob_url)}</dd>
        <dt>Skills loaded</dt><dd>${esc(h.skills)}</dd>
        <dt>Clone folder</dt><dd>${esc(h.workspace)}</dd>
      </dl>
      ${h.warnings.length ? `<ul class="warn-list">${h.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}`;
  };
  $("#saveSettings").onclick = () => {
    state.apiBase = $("#apiBase").value.trim().replace(/\/+$/, "");
    state.pollMs = Number($("#pollMs").value);
    store.set("rp.apiBase", state.apiBase);
    store.set("rp.pollMs", String(state.pollMs));
    store.set("rp.baseline", Number($("#baseline").value) > 0 ? String(Number($("#baseline").value)) : "");
    toast("Settings saved", "ok");
    showHealth(); refreshJobs();
  };
  $("#testConn").onclick = showHealth;
  showHealth();
};

// ------------------------------------------------------------------ job page
views.job = async (view, jobId, deepFile) => {
  view.innerHTML = `<div class="empty">Loading…</div>`;
  let job, skills;
  try {
    [job, skills] = await Promise.all([api(`/api/jobs/${encodeURIComponent(jobId)}`), api("/api/skills")]);
  } catch (e) {
    view.innerHTML = `<div class="empty">${e.status === 404 ? "This job no longer exists. Jobs are kept in memory, so restarting the backend clears them." : esc(e.message)}<br><br><a class="btn btn-soft btn-sm" href="#/">Back to overview</a></div>`;
    return;
  }
  const readmeSkill = skills.manual.find((s) => s.name === "readme-generator");
  const readmeDeps = readmeSkill ? readmeSkill.depends_on : [];
  state.openFile = null;
  state.userOpened = false;
  $("#crumb").textContent = repoName(job.repo.url);

  view.innerHTML = `
    <div class="job-head" id="jobHead"></div>
    <section class="card impact-card" id="impactCard" style="margin-bottom:18px">
      <div class="card-title-row"><h2 class="card-title">Impact</h2><span class="badge neutral">measured by the backend</span></div>
      <div id="impactBox"><div class="empty">Measuring…</div></div>
    </section>
    <div class="job-grid">
      <section class="card">
        <div class="card-title-row"><h2 class="card-title">Pipeline</h2><span class="title-actions"><button class="btn btn-soft btn-sm" type="button" id="retryFailed" hidden>↻ Retry failed</button><span id="pipeTime" class="step-time"></span></span></div>
        <p class="card-note">Runs after you confirm the clone. Click a step for Bob's summary, actions and files.</p>
        <div class="steps" id="steps"></div>
      </section>
      <div class="stack">
        <section class="card ide-card" id="ideCard">
          <div class="card-title-row"><h2 class="card-title">Continue in Bob IDE</h2><span class="badge neutral">.bob/</span></div>
          <div id="ideBox"></div>
        </section>
        <section class="card" id="qaCard">
          <div class="card-title-row"><h2 class="card-title">Ask Bob about this repo</h2><span class="badge neutral">codebase-qa</span></div>
          <form id="qaForm" class="qa-controls">
            <select id="qaRole" aria-label="Your role">${ROLES.map((r) => `<option>${esc(r)}</option>`).join("")}</select>
            <span class="qa-hint" style="align-self:center">Answers are shaped for your role.</span>
            <textarea id="qaInput" placeholder="Where is the database configured?" aria-label="Your question"></textarea>
            <div class="qa-actions"><span class="qa-hint">Ctrl + Enter to send</span><button class="btn btn-primary" type="submit" id="qaSend">Ask Bob <span class="button-arrow">→</span></button></div>
          </form>
          <div class="suggestions">${SUGGESTIONS.map((s) => `<button class="suggestion" type="button">${esc(s)}</button>`).join("")}</div>
          <div class="chat" id="chat"></div>
        </section>
        <section class="card" id="readmeCard">
          <div class="card-title-row"><h2 class="card-title">README</h2><span class="badge neutral">readme-generator</span></div>
          <div id="readmeBox"></div>
        </section>
      </div>
    </div>
    <section class="card" id="archCard" style="margin-top:18px" hidden>
      <div class="card-title-row"><h2 class="card-title">Architecture</h2><span class="badge neutral">from Bob's architecture.json</span></div>
      <p class="card-note">Click a component to see its files, endpoints and connections. Dashed columns are layers this repo doesn't have.</p>
      <div id="archBox"></div>
    </section>
    <section class="card" style="margin-top:18px">
      <div class="card-title-row"><h2 class="card-title">Onboarding files</h2><span class="title-actions"><span class="step-time" id="fileCount"></span><a class="btn btn-ghost btn-sm" id="packLink" hidden download>↓ Download pack (.zip)</a></span></div>
      <div class="file-layout"><div class="file-list" id="fileList"></div><div class="file-view" id="fileView"><div class="file-view-body"><div class="empty">Files appear here as the skills finish.</div></div></div></div>
    </section>
    ${footerHtml()}`;

  // ---- renderers
  const renderHead = () => {
    const r = job.repo;
    $("#jobHead").innerHTML = `
      <div>
        <p class="eyebrow">REPOSITORY · JOB ${esc(job.id)}</p>
        <h1>${esc(repoName(r.url))}</h1>
        <div class="facts">
          <a href="${esc(r.url.replace(/\.git$/, ""))}" target="_blank" rel="noopener">${esc(r.url)}</a>
          <span>⎇ ${esc(r.branch || "default")}</span>
          ${r.commits != null ? `<span>${esc(r.commits)} commits${r.depth ? " (shallow)" : ""}</span>` : ""}
          ${r.commit ? `<span title="${esc(r.commit.message)}">${esc(r.commit.hash.slice(0, 7))} · ${esc(r.commit.message.slice(0, 48))}</span>` : ""}
          <span>◷ ${esc(relTime(job.created_at))}</span>
        </div>
      </div>
      ${statusPill(job.status)}`;
  };

  const stepHtml = (name, s, t0, span) => {
    const st = s.status;
    const icon = { success: "✓", failed: "!", skipped: "–", pending: "·" }[st] || "";
    const out = s.output || {};
    const sub = st === "failed" || st === "skipped" ? (s.error || "").split("\n")[0] : snippet(out.summary) || skillDesc(name);
    const left = s.started_at ? ((s.started_at - t0) / span) * 100 : 0;
    const width = s.started_at ? Math.max(1.5, (((s.finished_at || Date.now() / 1000) - s.started_at) / span) * 100) : 0;
    const open = state.expanded.has(name);
    const canRetry = st === "failed" && job.repo.cloned && name !== "repo-clone";
    return `
      <div class="step ${open ? "expanded" : ""}" data-step="${esc(name)}">
        <button class="step-row" type="button" aria-expanded="${open}">
          <span class="step-icon ${esc(st)}">${icon}</span>
          <span><span class="step-name">${esc(skillName(name))}</span><span class="step-sub">${esc(sub)}</span></span>
          <span class="step-right"><span class="step-time">${(s.error || "").startsWith("Interrupted") ? "interrupted" : esc(fmtDur(s.started_at, s.finished_at))}</span>${statusPill(st)}</span>
        </button>
        ${s.started_at ? `<div class="step-track"><i class="${esc(st)}" style="left:${left.toFixed(2)}%;width:${Math.min(width, 100 - left).toFixed(2)}%"></i></div>` : ""}
        ${open ? `<div class="step-body">
          <div><h4>Skill</h4><code>${esc(name)}</code></div>
          ${out.summary ? `<div><h4>Bob's summary</h4><div class="md">${md(out.summary)}</div></div>` : ""}
          ${out.stats ? `<div class="step-sub">Bob run: ${esc(out.stats.tool_calls ?? "?")} tool calls · ${esc(((out.stats.duration_ms || 0) / 1000).toFixed(1))}s · cost ${esc(out.stats.session_costs ?? "?")}</div>` : ""}
          ${s.error ? `<div class="step-error">${esc(s.error)}</div>` : ""}
          ${out.actions_for_user && out.actions_for_user.length ? `<div><h4>Needs your action</h4><ul>${out.actions_for_user.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>` : ""}
          ${out.warnings && out.warnings.length ? `<div><h4>Warnings</h4><ul>${out.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
          ${s.files_written && s.files_written.length ? `<div><h4>Files</h4>${s.files_written.map((f) => {
            const v = out.validation && out.validation[f];
            const mark = v ? (v.length ? " ⚠" : " ✓") : "";
            return `<button class="file-chip ${v && v.length ? "bad" : ""}" type="button" data-file="${esc(f)}" title="${esc(v ? (v.length ? v.join("; ") : "passed the output check") : "")}">${esc(f)}${mark}</button>`;
          }).join("")}</div>` : ""}
          ${canRetry ? `<div><button class="btn btn-soft btn-sm" type="button" data-retry="${esc(name)}">↻ Retry this skill</button></div>` : ""}
        </div>` : ""}
      </div>`;
  };

  const renderSteps = () => {
    const names = PIPELINE.filter((n) => job.steps[n]).concat(Object.keys(job.steps).filter((n) => !PIPELINE.includes(n) && !["codebase-qa", "readme-generator"].includes(n)));
    const starts = Object.values(job.steps).map((s) => s.started_at).filter(Boolean);
    const ends = Object.values(job.steps).map((s) => s.finished_at || (s.started_at ? Date.now() / 1000 : null)).filter(Boolean);
    const t0 = starts.length ? Math.min(...starts) : 0;
    const span = Math.max(1, (ends.length ? Math.max(...ends) : t0 + 1) - t0);
    $("#steps").innerHTML = names.map((n) => stepHtml(n, job.steps[n], t0, span)).join("");
    const failedAuto = PIPELINE.slice(1).filter((n) => job.steps[n] && job.steps[n].status === "failed");
    const rb = $("#retryFailed");
    rb.hidden = !(failedAuto.length && job.repo.cloned);
    rb.textContent = `↻ Retry failed (${failedAuto.length})`;
    const pipeSteps = PIPELINE.map((n) => job.steps[n]).filter(Boolean);
    const done = pipeSteps.filter((s) => !ACTIVE.has(s.status)).length;
    $("#pipeTime").textContent = `${done}/${pipeSteps.length} done${starts.length ? " · " + fmtDur(t0, pipeSteps.every((s) => s.finished_at) ? Math.max(...pipeSteps.map((s) => s.finished_at)) : null) : ""}`;
  };

  const renderChat = () => {
    const qa = job.steps["codebase-qa"];
    const runs = qa ? qa.runs : [];
    const running = qa && qa.status === "running";
    const items = runs.slice().reverse().map((r) => {
      const q = (r.input && r.input.question) || "Role briefing";
      const role = r.input && r.input.role ? `${r.input.role} developer` : "Question";
      const answer = r.status === "success"
        ? `<div class="msg-a md">${md((r.output && (r.output.answer || r.output.summary)) || "Bob returned no answer text.")}</div>`
        : `<div class="msg-a error">${esc(r.error || "Failed")}</div>`;
      return `<div class="msg"><div class="msg-q"><small>${esc(role)}</small>${esc(q)}</div>${answer}</div>`;
    });
    if (running) {
      const pendingQ = state.pendingQuestion;
      items.unshift(`<div class="msg">${pendingQ ? `<div class="msg-q"><small>${esc(pendingQ.role)} developer</small>${esc(pendingQ.question)}</div>` : ""}<div class="msg-a pending">Bob is reading the code…</div></div>`);
    }
    const last = runs.slice().reverse().find((r) => r.status === "success");
    const follow = !running && last ? followUps((last.output && (last.output.answer || last.output.summary)) || "") : [];
    if (follow.length && items.length) {
      items[0] = items[0].replace(/<\/div>$/, `<div class="suggestions followups"><span class="qa-hint">Bob suggests next:</span>${follow.map((q) => `<button class="suggestion ask-now" type="button">${esc(q)}</button>`).join("")}</div></div>`);
    }
    $("#chat").innerHTML = items.join("") || `<div class="empty">Pick your role and ask anything about ${esc(repoName(job.repo.url))}.</div>`;
    const blocked = !job.repo.cloned || running;
    $("#qaSend").disabled = blocked;
    $("#qaSend").title = !job.repo.cloned ? "Available once the repository is cloned" : running ? "Bob is answering the previous question" : "";
  };

  const renderReadme = () => {
    const s = job.steps["readme-generator"];
    const deps = readmeDeps.map((d) => [d, (job.steps[d] || {}).status || "pending"]);
    const ready = job.repo.cloned && deps.every(([, st]) => st === "success");
    const running = s && s.status === "running";
    const hasReadme = s && s.files_written && s.files_written.includes("README.md");
    $("#readmeBox").innerHTML = `
      <p class="card-note" style="margin-top:0">Bob creates or completes the README from the verified results. It waits for:</p>
      <div class="kv" style="margin-bottom:14px">${deps.map(([d, st]) => `<span class="${st === "success" ? "accent" : ""}">${st === "success" ? "✓" : "…"} ${esc(skillName(d))}</span>`).join("")}</div>
      ${s && s.status === "failed" ? `<div class="step-error" style="margin-bottom:12px">${esc(s.error)}</div>` : ""}
      ${s && s.status === "success" && s.output && s.output.summary ? `<div class="md" style="margin-bottom:12px">${md(s.output.summary)}</div>` : ""}
      <div class="confirm-actions">
        <button class="btn btn-primary" type="button" id="readmeRun" ${!ready || running ? "disabled" : ""}>${running ? "Generating…" : s && s.status === "success" ? "↻ Generate again" : "Generate README"}</button>
        ${hasReadme ? `<button class="btn btn-ghost" type="button" data-file="README.md">Open README.md</button>` : ""}
      </div>
      ${!ready && !running ? `<p class="qa-hint" style="margin:10px 0 0">Available when the skills above have succeeded.</p>` : ""}`;
  };

  const renderImpact = async () => {
    let m;
    try { m = await api(`/api/jobs/${encodeURIComponent(jobId)}/metrics`); } catch { return; }
    const stand = !m.bob.cost && !m.bob.tool_calls;
    $("#impactBox").innerHTML = `
      <div class="impact-grid">
        ${tile("Time to onboard", m.time_to_onboard_seconds ? fmtSecs(m.time_to_onboard_seconds) : m.pipeline_seconds ? "—" : "running…",
               m.time_to_onboard_seconds ? `clone ${fmtSecs(m.clone_seconds)} · then Bob` : m.pipeline_seconds ? `pipeline had failures (${fmtSecs(m.pipeline_seconds)})` : "measuring")}
        ${tile("Skills completed", `${m.skills.succeeded}/${m.skills.total}`, m.skills.failed ? `${m.skills.failed} failed` : m.skills.running ? `${m.skills.running} running` : "no failures")}
        ${tile("Files produced", m.files_produced, m.outputs_checked ? `${m.outputs_valid}/${m.outputs_checked} passed the output check` : "onboarding/ + README")}
        ${tile("Secret findings", m.secret_findings.total, `history ${m.secret_findings.git_history} · current ${m.secret_findings.current_files}`)}
        ${tile("Bob tool calls", stand ? "—" : m.bob.tool_calls, `${m.bob.runs} Bob runs · ${fmtSecs(m.bob.seconds)} of Bob work`)}
        ${tile("Bob cost", stand ? "—" : Number(m.bob.cost).toFixed(2), stand ? "stand-in: no real Bob" : "Bob's own figure")}
      </div>
      ${compareHtml(m.time_to_onboard_seconds, m.manual_baseline && m.manual_baseline.manual_minutes)}
      <p class="qa-hint" style="margin:10px 0 0">Saved as <button class="file-chip" type="button" data-file="onboarding/metrics.json">onboarding/metrics.json</button> · secret counts are raw scanner findings; Bob's reports say which are false positives.</p>`;
  };

  // Retry every failed automatic skill, in dependency order (the backend refuses a skill
  // whose dependency is still running, so wait and try again).
  const retryFailed = async () => {
    const btn = $("#retryFailed");
    btn.disabled = true;
    const order = PIPELINE.slice(1).filter((n) => job.steps[n] && job.steps[n].status === "failed");
    for (const name of order) {
      for (let attempt = 0; attempt < 60; attempt++) {
        try {
          await api(`/api/repos/${encodeURIComponent(job.repo_id)}/skills/${encodeURIComponent(name)}/run`, { method: "POST" });
          break;
        } catch (ex) {
          if (ex.status === 409 && /finish first/.test(ex.message)) { await new Promise((r) => setTimeout(r, 2000)); continue; }
          toast(`${skillName(name)}: ${ex.message}`, "bad");
          break;
        }
      }
      poll();
    }
    btn.disabled = false;
    toast(`Retrying ${order.length} skill${order.length === 1 ? "" : "s"}…`);
  };

  // ---- architecture graph from architecture.json
  let arch = null, archSig = "";
  const LAYER_ORDER = ["client", "api", "core", "storage", "external"];
  const renderArch = async () => {
    let text;
    try { text = await api(`/api/repos/${encodeURIComponent(job.repo_id)}/files/onboarding/architecture.json`); } catch { return; }
    if (typeof text !== "string") text = JSON.stringify(text);
    if (text === archSig) return;
    archSig = text;
    try { arch = JSON.parse(text); } catch { $("#archCard").hidden = true; return; }
    const nodes = Array.isArray(arch.nodes) ? arch.nodes.filter((n) => n && n.id) : [];
    if (!nodes.length) { $("#archCard").hidden = true; return; }
    const layers = (Array.isArray(arch.layers) && arch.layers.length ? arch.layers : LAYER_ORDER.map((id) => ({ id, name: id, present: true })))
      .slice().sort((a, b) => (LAYER_ORDER.indexOf(a.id) + 99) % 99 - (LAYER_ORDER.indexOf(b.id) + 99) % 99);
    const known = new Set(layers.map((l) => l.id));
    const other = nodes.filter((n) => !known.has(n.layer));
    if (other.length) layers.push({ id: "__other", name: "Other", present: true });
    $("#archCard").hidden = false;
    $("#archBox").innerHTML = `
      ${arch.summary ? `<p class="arch-summary">${esc(arch.summary)}</p>` : ""}
      <div class="arch-layout" id="archLayout">
        <div class="arch-pane">
          <div class="arch-wrap"><div class="arch" id="arch">
            <svg class="arch-edges" id="archEdges" aria-hidden="true"></svg>
            <div class="arch-cols" style="grid-template-columns:repeat(${layers.length}, minmax(180px, 1fr))">
              ${layers.map((l) => {
                const ln = l.id === "__other" ? other : nodes.filter((n) => n.layer === l.id);
                const absent = l.present === false || !ln.length;
                return `<div class="arch-col ${absent ? "absent" : ""}"><h4>${esc(l.name || l.id)}</h4>
                  ${absent ? `<p class="arch-note">${esc(l.note || "Not present in this repo")}</p>` : ""}
                  ${ln.map((n) => `<button class="arch-node" type="button" data-node="${esc(n.id)}"><strong>${esc(n.label || n.id)}</strong><small>${esc((n.files || []).length)} file${(n.files || []).length === 1 ? "" : "s"}</small></button>`).join("")}
                </div>`;
              }).join("")}
            </div>
          </div></div>
        </div>
        <button class="arch-divider" id="archDivider" type="button" aria-label="Resize architecture details panel"></button>
        <aside class="arch-detail" id="archDetail"><span class="qa-hint">Select a component.</span></aside>
      </div>`;
    requestAnimationFrame(drawArchEdges);

    const divider = $("#archDivider");
    const layout = $("#archLayout");

    if (divider && layout) {
      divider.onpointerdown = (event) => {
        event.preventDefault();
        divider.setPointerCapture(event.pointerId);

        divider.onpointermove = (moveEvent) => {
          const rect = layout.getBoundingClientRect();
          const detailWidth = Math.max(
            240,
            Math.min(520, rect.right - moveEvent.clientX)
          );

          layout.style.setProperty(
            "--arch-detail-width",
            `${detailWidth}px`
          );

          requestAnimationFrame(drawArchEdges);
        };

        divider.onpointerup = () => {
          divider.releasePointerCapture(event.pointerId);
          divider.onpointermove = null;
        };
      };
    }

  };
  const drawArchEdges = () => {
    const box = $("#arch"), svg = $("#archEdges");
    if (!box || !svg || !arch) return;
    const b = box.getBoundingClientRect();
    svg.setAttribute("viewBox", `0 0 ${b.width} ${b.height}`);
    svg.setAttribute("width", b.width); svg.setAttribute("height", b.height);
    const pos = (id) => {
      const el = box.querySelector(`.arch-node[data-node="${CSS.escape(id)}"]`);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { l: r.left - b.left, r: r.right - b.left, y: r.top - b.top + r.height / 2, cx: r.left - b.left + r.width / 2 };
    };
    const paths = (arch.edges || []).map((e, i) => {
      const a = pos(e.from), c = pos(e.to);
      if (!a || !c) return "";
      let d;
      if (Math.abs(a.cx - c.cx) < 4) { const x = a.r - 6, bend = x + 40; d = `M ${x} ${a.y} C ${bend} ${a.y}, ${bend} ${c.y}, ${x} ${c.y}`; }
      else if (a.cx < c.cx) { const m = (a.r + c.l) / 2; d = `M ${a.r} ${a.y} C ${m} ${a.y}, ${m} ${c.y}, ${c.l} ${c.y}`; }
      else { const m = (a.l + c.r) / 2; d = `M ${a.l} ${a.y} C ${m} ${a.y}, ${m} ${c.y}, ${c.r} ${c.y}`; }
      return `<path d="${d}" data-from="${esc(e.from)}" data-to="${esc(e.to)}" marker-end="url(#arrow)"><title>${esc(`${e.from} → ${e.to}${e.label ? ": " + e.label : ""}`)}</title></path>`;
    }).join("");
    svg.innerHTML = `<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor"/></marker></defs>${paths}`;
  };
  const selectArchNode = (id) => {
    const n = (arch.nodes || []).find((x) => x.id === id);
    if (!n) return;
    document.querySelectorAll(".arch-node").forEach((el) => el.classList.toggle("active", el.dataset.node === id));
    document.querySelectorAll("#archEdges path[data-from]").forEach((p) => p.classList.toggle("hot", p.dataset.from === id || p.dataset.to === id));
    const label = (x) => ((arch.nodes || []).find((m) => m.id === x) || {}).label || x;
    const out = (arch.edges || []).filter((e) => e.from === id), inc = (arch.edges || []).filter((e) => e.to === id);
    $("#archDetail").innerHTML = `
      <h3>${esc(n.label || n.id)} <span class="kv"><span class="accent">${esc(n.layer || "")}</span></span></h3>
      ${n.description ? `<p>${esc(n.description)}</p>` : ""}
      ${(n.files || []).length ? `<h4>Files</h4><div class="kv">${n.files.map((f) => `<span>${esc(typeof f === "string" ? f : f.path)}${f.lines ? ":" + esc(f.lines) : ""}</span>`).join("")}</div>` : ""}
      ${(n.endpoints || []).length ? `<h4>Endpoints</h4><div class="kv">${n.endpoints.map((x) => `<span>${esc(typeof x === "string" ? x : JSON.stringify(x))}</span>`).join("")}</div>` : ""}
      ${out.length ? `<h4>Uses</h4><ul>${out.map((e) => `<li>${esc(label(e.to))}${e.label ? ` <code>${esc(e.label)}</code>` : ""}</li>`).join("")}</ul>` : ""}
      ${inc.length ? `<h4>Used by</h4><ul>${inc.map((e) => `<li>${esc(label(e.from))}${e.label ? ` <code>${esc(e.label)}</code>` : ""}</li>`).join("")}</ul>` : ""}`;
  };
  window.addEventListener("resize", () => requestAnimationFrame(drawArchEdges));

  const renderIde = () => {
    const h = job.bob_ide;
    const box = $("#ideBox");
    if (!job.repo.cloned || !h) { box.innerHTML = `<p class="card-note" style="margin:0">Available once the repository is cloned.</p>`; return; }
    if (h.error) { box.innerHTML = `<div class="step-error">${esc(h.error)}</div>`; return; }
    const launcher = state.health && state.health.bob_ide;
    box.innerHTML = `
      <p class="card-note" style="margin-top:0">This clone is ready for Bob IDE: the same <strong>${esc(h.skills.length)} skills</strong>, a <strong>Codebase Q&amp;A</strong> mode, and every file Bob wrote in <code>onboarding/</code>. ${h.installed.includes(".bobignore") ? `Secret files (keys, certificates, credentials) are hidden from Bob by <code>.bobignore</code>.` : ""}</p>
      <div class="confirm-actions" style="margin-bottom:12px">
        <button class="btn btn-primary" type="button" id="openBob" ${launcher ? "" : "disabled title=\"Bob IDE launcher not found on this computer\""}>Open in Bob IDE <span class="button-arrow">↗</span></button>
        <button class="btn btn-ghost" type="button" id="copyPath">Copy folder path</button>
      </div>
      <div class="kv" style="margin-bottom:10px">${h.skills.map((n) => `<span class="accent">/${esc(n)}</span>`).join("")}</div>
      <p class="qa-hint" style="margin:0">In Bob IDE, pick <strong>Codebase Q&amp;A</strong> in the mode list, or type a skill such as <code>/codebase-qa</code> in the chat. Folder: <code>${esc(h.workspace_path)}</code>${h.kept_repo_files.length ? ` · kept ${esc(h.kept_repo_files.length)} existing .bob file(s) from the repo` : ""}</p>`;
  };

  // ---- files
  let fileSig = "";
  const fileIcon = (f) => (f.endsWith(".json") ? "▦" : f.endsWith(".mmd") ? "◇" : "¶");
  const renderFileList = async () => {
    let files = [];
    try { files = (await api(`/api/repos/${encodeURIComponent(job.repo_id)}/files`)).files; } catch { return; }
    const sig = files.join("|");
    if (sig === fileSig) return;
    fileSig = sig;
    $("#fileCount").textContent = `${files.length} file${files.length === 1 ? "" : "s"}`;
    const first = ["onboarding/ONBOARDING_REPORT.md", "README.md"];
    const byName = (a, b) => (first.indexOf(b) - first.indexOf(a)) || fileLabel(a).localeCompare(fileLabel(b));
    const groups = [
      ["Reports", files.filter((f) => f.endsWith(".md")).sort(byName)],
      ["Diagrams", files.filter((f) => f.endsWith(".mmd"))],
      ["Data for the app (shown readable)", files.filter((f) => f.endsWith(".json")).sort(byName)],
      ["Other", files.filter((f) => !/\.(md|mmd|json)$/.test(f))],
    ].filter(([, list]) => list.length);
    const item = (f) => `<button class="file-item ${state.openFile === f ? "active" : ""}" type="button" data-file="${esc(f)}" title="${esc(f)}"><span>${fileIcon(f)}</span><span class="file-name"><strong>${esc(fileLabel(f))}</strong><small>${esc(f.replace("onboarding/", ""))}</small></span></button>`;
    $("#fileList").innerHTML = files.length
      ? groups.map(([label, list]) => `<div class="file-group-label">${esc(label)}</div>${list.map(item).join("")}`).join("")
      : `<div class="empty">No files yet.</div>`;
    if (deepFile && files.includes(deepFile) && !state.userOpened) { state.userOpened = true; openFile(deepFile); }  // #/job/<id>/file/<path>
    const preferred = first.find((f) => files.includes(f)) || (files.includes("onboarding/tech_stack.md") ? "onboarding/tech_stack.md" : files[0]);
    if (files.length && (!state.openFile || (state.openFile !== preferred && preferred === first[0] && !state.userOpened))) openFile(preferred, false);
    const pack = $("#packLink");
    pack.hidden = !files.length;
    pack.href = `${state.apiBase}/api/repos/${encodeURIComponent(job.repo_id)}/pack.zip`;
    if (files.includes("onboarding/architecture.json")) renderArch();
  };
  const openFile = async (path, scroll = true) => {
    state.openFile = path;
    document.querySelectorAll(".file-item").forEach((b) => b.classList.toggle("active", b.dataset.file === path));
    const box = $("#fileView");
    box.innerHTML = `<div class="file-view-head"><span><strong>${esc(fileLabel(path))}</strong> <span class="step-time">${esc(path)}</span></span></div><div class="file-view-body"><div class="empty">Loading…</div></div>`;
    if (scroll) box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    let text;
    try { text = await api(`/api/repos/${encodeURIComponent(job.repo_id)}/files/${path.split("/").map(encodeURIComponent).join("/")}`); }
    catch (e) { $(".file-view-body", box).innerHTML = `<div class="step-error">${esc(e.message)}</div>`; return; }
    if (typeof text !== "string") text = JSON.stringify(text, null, 2);
    const body = $(".file-view-body", box);
    const head = $(".file-view-head", box);
    head.insertAdjacentHTML("beforeend", `<button class="btn btn-ghost btn-sm" type="button" id="copyFile">Copy</button>`);
    $("#copyFile").onclick = async () => {
      try { await navigator.clipboard.writeText(text); toast("Copied", "ok"); } catch { toast("Copy blocked by the browser", "bad"); }
    };
    if (path.endsWith(".md")) body.innerHTML = `<div class="md">${md(text)}</div>`;
    else if (path.endsWith(".json")) {
      let data = null, pretty = text;
      try { data = JSON.parse(text); pretty = JSON.stringify(data, null, 2); } catch { /* not valid JSON: show as-is */ }
      if (data === null) { body.innerHTML = `<pre class="raw">${esc(pretty)}</pre>`; return; }
      head.insertAdjacentHTML("beforeend", `<button class="btn btn-ghost btn-sm" type="button" id="rawToggle">Raw JSON</button>`);
      let raw = false;
      const show = () => { body.innerHTML = raw ? `<pre class="raw">${esc(pretty)}</pre>` : jsonReport(data, path); $("#rawToggle").textContent = raw ? "Readable view" : "Raw JSON"; };
      $("#rawToggle").onclick = () => { raw = !raw; show(); };
      show();
    } else if (path.endsWith(".mmd")) {
      body.innerHTML = `<div class="mermaid-box" id="mmd"><pre class="raw">${esc(text)}</pre></div>`;
      try {
        const mermaid = await loadMermaid();
        const { svg } = await mermaid.render(`m${Date.now()}`, text);
        if (state.openFile === path) $("#mmd").innerHTML = svg;
      } catch { /* keep the raw diagram text */ }
    } else body.innerHTML = `<pre class="raw">${esc(text)}</pre>`;
  };

  // ---- events (delegated, so they survive re-renders)
  view.addEventListener("click", async (e) => {
    const stepBtn = e.target.closest(".step-row");
    const fileBtn = e.target.closest("[data-file]");
    const retry = e.target.closest("[data-retry]");
    const sugg = e.target.closest(".suggestion");
    if (retry) {
      e.stopPropagation();
      try { await api(`/api/repos/${encodeURIComponent(job.repo_id)}/skills/${encodeURIComponent(retry.dataset.retry)}/run`, { method: "POST" }); toast(`Retrying ${skillName(retry.dataset.retry)}…`); poll(); }
      catch (ex) { toast(ex.message, "bad"); }
    } else if (fileBtn) {
      state.userOpened = true;
      openFile(fileBtn.dataset.file);
    } else if (stepBtn) {
      const name = stepBtn.closest(".step").dataset.step;
      state.expanded.has(name) ? state.expanded.delete(name) : state.expanded.add(name);
      renderSteps();
    } else if (sugg) {
      $("#qaInput").value = sugg.textContent;
      if (sugg.classList.contains("ask-now") && !$("#qaSend").disabled) $("#qaForm").requestSubmit();
      else $("#qaInput").focus();
    } else if (e.target.closest("#retryFailed")) {
      retryFailed();
    } else if (e.target.closest(".arch-node")) {
      selectArchNode(e.target.closest(".arch-node").dataset.node);
    } else if (e.target.closest("#openBob")) {
      try { await api(`/api/repos/${encodeURIComponent(job.repo_id)}/open-in-bob`, { method: "POST" }); toast("Opening Bob IDE…", "ok"); }
      catch (ex) { toast(ex.message, "bad"); }
    } else if (e.target.closest("#copyPath")) {
      try { await navigator.clipboard.writeText(job.bob_ide.workspace_path); toast("Folder path copied", "ok"); }
      catch { toast(job.bob_ide.workspace_path); }
    } else if (e.target.id === "readmeRun") {
      e.target.disabled = true;
      try { await api(`/api/repos/${encodeURIComponent(job.repo_id)}/skills/readme-generator/run`, { method: "POST" }); toast("Generating README…"); poll(); }
      catch (ex) { toast(ex.message, "bad"); e.target.disabled = false; }
    }
  });
  $("#qaInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#qaForm").requestSubmit(); });
  $("#qaForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const question = $("#qaInput").value.trim();
    const role = $("#qaRole").value;
    if (!question) { $("#qaInput").focus(); return; }
    $("#qaSend").disabled = true;
    try {
      await api(`/api/repos/${encodeURIComponent(job.repo_id)}/skills/codebase-qa/run`, { method: "POST", body: JSON.stringify({ role, question }) });
      state.pendingQuestion = { role, question };
      $("#qaInput").value = "";
      poll();
    } catch (ex) {
      toast(ex.message, "bad");
      $("#qaSend").disabled = false;
    }
  });

  // ---- live updates
  let finishedSig = "";
  const renderAll = () => {
    renderHead(); renderSteps(); renderIde(); renderChat(); renderReadme();
    const sig = Object.values(job.steps).map((s) => s.status + (s.files_written || []).length).join("|");
    if (sig !== finishedSig) { finishedSig = sig; renderFileList(); renderImpact(); }
  };
  let wasActive = ACTIVE.has(job.status) || job.status === "queued";
  const poll = async () => {
    try {
      job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      const active = ACTIVE.has(job.status) || job.status === "queued";
      if (wasActive && !active) {
        const pipe = PIPELINE.map((n) => job.steps[n]).filter(Boolean);
        const ok = pipe.every((s) => s.status === "success");
        const secs = ok ? Math.max(...pipe.map((s) => s.finished_at)) - Math.min(...pipe.map((s) => s.started_at)) : 0;
        toast(ok ? `Onboarding finished in ${fmtSecs(secs)}. Ask Bob anything, or continue in Bob IDE.`
                 : `Onboarding finished with ${pipe.filter((s) => s.status !== "success").length} failed step(s). Use Retry failed.`, ok ? "ok" : "bad");
      }
      wasActive = active;
      if (!(job.steps["codebase-qa"] && job.steps["codebase-qa"].status === "running")) state.pendingQuestion = null;
      renderAll();
    } catch { /* keep the last view; health shows offline */ }
  };
  renderAll();
  let lastTick = 0;
  every(500, () => {
    const busy = ACTIVE.has(job.status) || Object.values(job.steps).some((s) => s.status === "running");
    const wait = busy ? state.pollMs : 5000;
    if (Date.now() - lastTick >= wait) { lastTick = Date.now(); poll(); }
  });
};

// ------------------------------------------------------------------ router
const ROUTES = [
  [/^#?\/?$/, "home", "Overview", "home"],
  [/^#\/repos$/, "repos", "Repositories", "repos"],
  [/^#\/skills$/, "skills", "Skills", "skills"],
  [/^#\/settings$/, "settings", "Settings", "settings"],
  [/^#\/job\/([\w-]+)(?:\/file\/(.+))?$/, "job", "Repository", "repos"],
];
async function route() {
  clearTimers();
  state.expanded.clear();
  const hash = location.hash || "#/";
  const match = ROUTES.find(([re]) => re.test(hash)) || ROUTES[0];
  const [re, view, crumb, nav] = match;
  const [, arg, arg2] = hash.match(re) || [];
  $("#crumb").textContent = crumb;
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
  const el = $("#view");
  el.replaceWith(el.cloneNode(false)); // drop old listeners
  const fresh = $("#view");
  window.scrollTo({ top: 0 });
  refreshJobs(); // keeps the sidebar repo count right on every page
  await views[view](fresh, arg, arg2 && decodeURIComponent(arg2));
}

$("#navClone").addEventListener("click", (e) => {
  state.focusClone = true;
  if ((location.hash || "#/") === "#/" || location.hash === "") { e.preventDefault(); const i = $("#repoUrl"); if (i) { i.scrollIntoView({ behavior: "smooth", block: "center" }); i.focus(); } }
});
window.addEventListener("hashchange", route);
refreshHealth();
setInterval(refreshHealth, 15000);
route();
