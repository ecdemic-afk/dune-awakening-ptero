"use strict";
// Dune server web panel. Talks only to /api/*; every action is a console command.

const $ = (id) => document.getElementById(id);
let lastSeq = 0;
let status = null;
let configDirty = false;
let timers = [];

async function api(path, body) {
  const opts = body === undefined
    ? { credentials: "same-origin" }
    : { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Dune": "1" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Error("signed out"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { const e = new Error(data.error || `HTTP ${res.status}`); e.data = data; throw e; }
  return data;
}

// ---- sign in -------------------------------------------------------------
function showLogin() {
  timers.forEach(clearInterval); timers = [];
  $("app").hidden = true; $("login").hidden = false; $("password").focus();
}
$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("login-error").textContent = "";
  try {
    await api("/api/login", { password: $("password").value });
    $("password").value = "";
    start();
  } catch (e) { $("login-error").textContent = e.message; }
});
$("logout").addEventListener("click", async () => { await api("/api/logout", {}).catch(() => {}); showLogin(); });

// ---- tabs ----------------------------------------------------------------
// Five sections in the header; a section with several pages shows them in a second row.
const GROUPS = {
  overview: [["overview", "Overview"]],
  players: [["players", "Players"], ["items", "Items"], ["vehicles", "Vehicles"], ["kits", "Kits & gifts"]],
  world: [["map", "Map"], ["bases", "Bases"], ["guilds", "Guilds"]],
  server: [["config", "Settings"], ["schedule", "Schedule"], ["backups", "Backups"], ["commands", "Commands"]],
  console: [["console", "Console"]],
};
const groupOf = (page) => Object.keys(GROUPS).find((g) => GROUPS[g].some(([p]) => p === page)) || "overview";
const lastPage = {};
document.querySelectorAll("[data-group]").forEach((b) => b.addEventListener("click", () => {
  const g = b.dataset.group;
  openTab(lastPage[g] || GROUPS[g][0][0]);
}));
function openTab(name) {
  if (!document.getElementById(`tab-${name}`)) name = "overview";
  const group = groupOf(name);
  lastPage[group] = name;
  document.querySelectorAll("[data-group]").forEach((b) => {
    if (b.dataset.group === group) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  const sub = $("subnav");
  sub.hidden = GROUPS[group].length < 2;
  sub.replaceChildren(...GROUPS[group].map(([page, label]) => {
    const b = document.createElement("button");
    b.textContent = label;
    if (page === name) b.setAttribute("aria-current", "page");
    b.addEventListener("click", () => openTab(page));
    return b;
  }));
  document.querySelectorAll(".tab").forEach((s) => { s.hidden = s.id !== `tab-${name}`; });
  if (name === "config" && !configDirty) loadConfig();
  if (name === "players" && window.loadPlayers) window.loadPlayers();
  if (window.WORLD && window.WORLD[name]) window.WORLD[name]();
  if (name === "console") scrollLog();
  try { localStorage.setItem("dune-tab", name); } catch (_) {}
}

// ---- status --------------------------------------------------------------
function duration(s) {
  if (!s) return "–";
  if (s < 60) return `${s}s`;
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}
function li(markClass, mark, text, fix) {
  const el = document.createElement("li");
  const m = document.createElement("span"); m.className = `mark ${markClass}`; m.textContent = mark;
  const t = document.createElement("span"); t.textContent = text;
  el.append(m, t);
  if (fix) { const f = document.createElement("span"); f.className = "fix"; f.textContent = fix; el.append(f); }
  return el;
}
async function refreshStatus() {
  try { status = await api("/api/status"); } catch (_) { return; }
  $("server-name").textContent = status.server_name || "Dune server";
  document.title = `${status.server_name || "Dune"} — panel`;
  const st = $("state"); st.textContent = status.state; st.className = `badge ${status.state}`;
  $("players").textContent = status.players ?? "–";
  $("joinable").textContent = status.state !== "running" ? "no" : status.joinable ? "yes" : "loading maps…";
  $("uptime").textContent = duration(status.uptime);
  $("version").textContent = status.version;

  const p = status.pending;
  $("pending").hidden = !p;
  if (p) $("pending-text").textContent = `${p.kind} in ${p.in ? duration(p.in) : "a moment"}${p.reason ? " — " + p.reason : ""}`;

  const checks = $("checks"); checks.replaceChildren();
  for (const c of status.checks || []) {
    const [cls, mark] = c.status === "ok" ? ["ok", "✔"] : c.status === "warn" ? ["warn", "!"] : ["bad", "✘"];
    checks.append(li(cls, mark, `${c.name}: ${c.message}`, c.status !== "ok" ? c.fix : ""));
  }
  const svc = $("services"); svc.replaceChildren();
  for (const [name, up] of Object.entries(status.services || {})) svc.append(li(up ? "ok" : "bad", up ? "●" : "○", name));

  const list = $("backups"); list.replaceChildren();
  if (!(status.backups || []).length) list.append(li("", "", "No backups yet."));
  for (const file of status.backups || []) {
    const row = document.createElement("li");
    const name = document.createElement("code"); name.textContent = file;
    const btn = document.createElement("button"); btn.className = "danger"; btn.textContent = "Restore…";
    btn.addEventListener("click", () => {
      if (confirm(`Restore ${file}?\n\nThe world goes back to that moment. The current world is saved as a new backup first, and the server restarts.`))
        run(`restore ${file} confirm`);
    });
    row.append(name, btn); list.append(row);
  }
}

// ---- commands ------------------------------------------------------------
async function run(line) {
  $("action-result").textContent = "";
  try {
    await api("/api/command", { line });
    $("action-result").textContent = `Sent: ${line} — progress is in the Console tab.`;
    setTimeout(refreshStatus, 1500);
  } catch (e) { $("action-result").textContent = e.message; }
}
document.querySelectorAll("[data-cmd]").forEach((b) => b.addEventListener("click", () => run(b.dataset.cmd)));
document.querySelectorAll("[data-timed]").forEach((b) => b.addEventListener("click", () => {
  const when = $("countdown").value, reason = $("reason").value.trim();
  const label = { restart: "Restart the server", update: "Install a game update and restart", upgrade: "Upgrade the server software and restart" }[b.dataset.timed];
  if (!confirm(`${label}${when === "now" ? " now" : `, with a ${when} countdown if players are on`}?`)) return;
  run(`${b.dataset.timed} ${when === "now" ? "now" : "in " + when}${reason ? " " + reason : ""}`);
}));
$("announce-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const msg = $("announce").value.trim();
  if (msg) { run(`announce ${msg}`); $("announce").value = ""; }
});

// ---- console -------------------------------------------------------------
function scrollLog() { if ($("follow").checked) $("log").scrollTop = $("log").scrollHeight; }
function renderLine(r) {
  const row = document.createElement("div");
  row.className = r.level; row.dataset.level = r.level;
  const tag = document.createElement("span"); tag.className = "tag"; tag.textContent = `[${r.tag}] `;
  row.append(tag, document.createTextNode((r.level !== "INFO" ? `[${r.level}] ` : "") + r.line));
  row.hidden = $("only-problems").checked && r.level === "INFO";
  return row;
}
async function refreshLog() {
  let data;
  try { data = await api(`/api/log?after=${lastSeq}`); } catch (_) { return; }
  if (!data.lines.length) return;
  const log = $("log"), frag = document.createDocumentFragment();
  for (const r of data.lines) { frag.append(renderLine(r)); lastSeq = r.seq; }
  log.append(frag);
  while (log.childElementCount > 3000) log.firstElementChild.remove();
  scrollLog();
}
$("only-problems").addEventListener("change", () => {
  for (const row of $("log").children) row.hidden = $("only-problems").checked && row.dataset.level === "INFO";
});
$("command-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const line = $("command").value.trim();
  if (!line) return;
  $("command").value = "";
  try { await api("/api/command", { line }); }
  catch (e) { $("log").append(renderLine({ tag: "panel", level: "WARN", line: e.message })); scrollLog(); }
});

// ---- config --------------------------------------------------------------
async function loadConfig() {
  const data = await api("/api/config");
  $("config").value = data.text; configDirty = false; $("config-problems").replaceChildren();
}
$("config").addEventListener("input", () => { configDirty = true; });
$("config-reload").addEventListener("click", () => { if (!configDirty || confirm("Discard your changes?")) loadConfig(); });
async function saveConfig(thenRestart) {
  const problems = $("config-problems"); problems.replaceChildren();
  try {
    await api("/api/config", { text: $("config").value });
    configDirty = false;
    problems.append(Object.assign(document.createElement("li"), { textContent: "Saved." }));
    problems.style.color = "var(--ok)";
    if (thenRestart) run("restart");
  } catch (e) {
    problems.style.color = "";
    for (const p of (e.data && e.data.problems) || [e.message])
      problems.append(Object.assign(document.createElement("li"), { textContent: p }));
  }
}
$("config-save").addEventListener("click", () => saveConfig(false));
$("config-save-restart").addEventListener("click", () => saveConfig(true));
window.addEventListener("beforeunload", (ev) => { if (configDirty) ev.preventDefault(); });

// ---- start ---------------------------------------------------------------
async function start() {
  try { await api("/api/status"); } catch (_) { return; }  // 401 shows the sign-in form
  $("login").hidden = true; $("app").hidden = false;
  let tab = "overview";
  try { tab = localStorage.getItem("dune-tab") || tab; } catch (_) {}
  openTab(tab);
  refreshStatus(); refreshLog();
  timers.forEach(clearInterval);
  timers = [setInterval(refreshStatus, 5000), setInterval(refreshLog, 2000)];
}
window.addEventListener("DOMContentLoaded", start);  // after players.js and world.js have loaded
$("ov-health").addEventListener("click", async () => {
  $("action-result").textContent = "Running the health check…";
  try { await api("/api/info/health"); await refreshStatus(); $("action-result").textContent = "Health check finished — results under Health below."; }
  catch (e) { $("action-result").textContent = e.message; }
});
