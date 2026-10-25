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

// ---- navigation ----------------------------------------------------------
// Line icons (24x24, stroked). Kept here so the page needs no icon font or CDN.
const ICONS = {
  home: "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
  box: "M21 8l-9-5-9 5v8l9 5 9-5zM3 8l9 5 9-5M12 13v8",
  car: "M5 17h14M5 17a2 2 0 1 1-4 0v-5l3-6h16l3 6v5a2 2 0 1 1-4 0M7 17a2 2 0 1 0 4 0M13 17a2 2 0 1 0 4 0M3 12h18",
  gift: "M20 12v9H4v-9M2 7h20v5H2zM12 21V7M12 7H7.5a2.5 2.5 0 1 1 0-5C11 2 12 7 12 7zM12 7h4.5a2.5 2.5 0 1 0 0-5C13 2 12 7 12 7z",
  map: "M9 4L3 6v14l6-2 6 2 6-2V4l-6 2zM9 4v14M15 6v14",
  layers: "M12 2l10 5-10 5L2 7zM2 17l10 5 10-5M2 12l10 5 10-5",
  base: "M3 21h18M5 21V10l7-5 7 5v11M9 21v-6h6v6",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  sliders: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
  clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2",
  archive: "M21 8v13H3V8M1 3h22v5H1zM10 12h4",
  bolt: "M13 2L3 14h9l-1 8 10-12h-9z",
  terminal: "M4 17l6-6-6-6M12 19h8",
  door: "M13 4h3a2 2 0 0 1 2 2v14M2 20h3M13 20h9M10 12v.01M13 4.6v16.6L6 20V5.6z",
  tag: "M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0L2 12V2h10l8.6 8.6a2 2 0 0 1 0 2.8zM7 7h.01",
  menu: "M3 6h18M3 12h18M3 18h18",
};
function icon(name, cls = "ico") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", cls); s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", ICONS[name] || ""); s.append(p);
  return s;
}
// [group, [[page, label, icon, description], ...]]
const NAV = [
  ["", [["overview", "Overview", "home", "Server state, quick actions and health at a glance."]]],
  ["Players", [
    ["players", "Players", "users", "Find a player by name; inventory, progression, bans and more."],
    ["items", "Items", "box", "The whole item catalogue, with pictures. Give anything to anyone."],
    ["vehicles", "Vehicles", "car", "Spawn a vehicle next to a player."],
    ["kits", "Kits & gifts", "gift", "Item bundles, gifts to everyone online, and the welcome gift."]]],
  ["World", [
    ["map", "Map", "map", "Where everyone is, teleports, saved spots, resources and ship crash sites."],
    ["instances", "Instances", "layers", "Every running map with its players and memory. Start or stop maps."],
    ["bases", "Bases", "base", "Every base, its water and power. Refills."],
    ["guilds", "Guilds", "shield", "Members, leaders, invites."]]],
  ["Server", [
    ["config", "Settings", "sliders", "Every server and game setting, explained."],
    ["schedule", "Schedule", "clock", "Automatic restarts, backups and announcements."],
    ["backups", "Backups", "archive", "Save the world, or bring an older one back."],
    ["commands", "Commands", "bolt", "Server information and every game operation as a form."],
    ["console", "Console", "terminal", "Live output from every part of the server."]]],
];
const PAGE = Object.fromEntries(NAV.flatMap(([, pages]) => pages.map((p) => [p[0], p])));
$("side-nav").replaceChildren(...NAV.map(([group, pages]) => {
  const box = document.createElement("div");
  box.className = "nav-group";
  if (group) box.append(Object.assign(document.createElement("span"), { className: "nav-label", textContent: group }));
  for (const [page, label, ico] of pages) {
    const b = document.createElement("button");
    b.type = "button"; b.dataset.page = page;
    b.append(icon(ico), document.createTextNode(label));
    b.addEventListener("click", () => openTab(page));
    box.append(b);
  }
  return box;
}));
document.querySelectorAll("[data-icon]").forEach((e) => e.append(icon(e.dataset.icon)));
$("menu-btn").append(icon("menu"));
function setMenu(open) {
  document.body.classList.toggle("menu-open", open);
  $("scrim").hidden = !open;
  $("menu-btn").setAttribute("aria-expanded", String(open));
}
$("menu-btn").addEventListener("click", () => setMenu(!document.body.classList.contains("menu-open")));
$("scrim").addEventListener("click", () => setMenu(false));
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") setMenu(false); });

function openTab(name) {
  if (!document.getElementById(`tab-${name}`)) name = "overview";
  const [, label, , desc] = PAGE[name] || ["", name, "", ""];
  $("page-title").textContent = label;
  $("page-desc").textContent = desc;
  document.querySelectorAll("#side-nav [data-page]").forEach((b) => {
    if (b.dataset.page === name) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  setMenu(false);
  window.scrollTo(0, 0);
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
  $("side-version").textContent = `v${status.version}`;

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
