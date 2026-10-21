"use strict";
// Map, Bases, Guilds, Schedule and Commands pages. Uses $, api, run from app.js and el from players.js.

const W = { maps: [], map: null, markers: [], mapTimer: null, bases: [], base: null, guilds: [], guild: null,
            schedule: null, ops: [], op: null };

function table(rows, columns) {
  // columns: [[key, label, fmt?]]; unknown columns are not shown.
  if (!rows.length) return [el("tr", {}, el("td", { className: "muted", textContent: "None." }))];
  const head = el("tr", {}, ...columns.map(([, label]) => el("th", { textContent: label })));
  return [head, ...rows.map((r) => el("tr", {}, ...columns.map(([k, , f]) => el("td", { textContent: f ? f(r[k], r) : (r[k] ?? "") }))))];
}
function showResult(id, r) {
  const out = $(id);
  out.className = `result ${r.ok ? "ok" : "bad"}`;
  out.textContent = r.output || r.error || "";
}
async function post(path, body, resultId) {
  const out = $(resultId); out.className = "result"; out.textContent = "Working…";
  try { const r = await api(path, body); showResult(resultId, r); return r; }
  catch (e) { showResult(resultId, { ok: false, error: e.message }); return null; }
}

// ---- map -------------------------------------------------------------------
function project(x, y, m) {
  const nx = (x - m.minX) / (m.maxX - m.minX), ny = (y - m.minY) / (m.maxY - m.minY);
  return { left: Math.min(1, Math.max(0, nx)) * 100, top: Math.min(1, Math.max(0, ny)) * 100,
           inside: nx >= 0 && nx <= 1 && ny >= 0 && ny <= 1 };
}
function sector(p) {  // Deep Desert 9x9 grid: rows I..A top to bottom, columns 1..9
  const rows = ["I", "H", "G", "F", "E", "D", "C", "B", "A"];
  return rows[Math.min(8, Math.floor(p.top / 100 * 9))] + (Math.min(8, Math.floor(p.left / 100 * 9)) + 1);
}
async function loadMapPage() {
  if (!W.maps.length) {
    try { W.maps = (await api("/api/maps")).maps; } catch (e) { $("map-error").textContent = e.message; return; }
    $("map-buttons").replaceChildren(...W.maps.map((m) => {
      const b = el("button", { type: "button", textContent: m.label });
      b.addEventListener("click", () => selectMap(m.key));
      b.dataset.key = m.key;
      return b;
    }));
    let saved = null; try { saved = localStorage.getItem("dune-map"); } catch (_) {}
    W.map = W.maps.find((m) => m.key === saved) || W.maps[0];
  }
  selectMap(W.map.key);
  clearInterval(W.mapTimer);
  W.mapTimer = setInterval(() => { if (!$("tab-map").hidden) refreshMarkers(); }, 10000);
}
function selectMap(key) {
  W.map = W.maps.find((m) => m.key === key);
  try { localStorage.setItem("dune-map", key); } catch (_) {}
  document.querySelectorAll("#map-buttons button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.key === key)));
  $("map-img").src = W.map.image; $("map-img").alt = `${W.map.label} map`;
  $("map-note").textContent = W.map.note ? `Note: ${W.map.note}.` : "";
  W.markers = []; drawMarkers(); refreshMarkers();
  clearPick(); loadResources();
}

// Deep Desert: 9x9 sector grid (rows I..A from the top, columns 1..9) and the week's points of interest.
const DD_ROWS = ["I", "H", "G", "F", "E", "D", "C", "B", "A"];
const ICON = (t) => `maps/icons/${t}.${t === "taxi-service" ? "png" : "svg"}`;
function drawGrid(on) {
  const g = $("map-grid"); g.replaceChildren();
  if (!on) return;
  for (let i = 1; i < 9; i++) {
    g.append(el("div", { className: "gline v", style: `left:${i / 9 * 100}%` }), el("div", { className: "gline h", style: `top:${i / 9 * 100}%` }));
  }
  DD_ROWS.forEach((r, ri) => g.append(el("span", { className: "glabel", style: `left:.3%;top:${ri / 9 * 100 + 0.4}%`, textContent: r })));
  for (let c = 1; c <= 9; c++) g.append(el("span", { className: "glabel", style: `left:${(c - 0.5) / 9 * 100}%;top:.3%`, textContent: c }));
}
async function loadResources() {
  const key = W.map.key;
  W.resources = null; $("map-pois").replaceChildren(); $("map-legend").replaceChildren();
  $("map-res-toggle").hidden = key !== "DeepDesert";
  drawGrid(key === "DeepDesert");
  if (key !== "DeepDesert") return;
  let r;
  try { r = await api(`/api/map/${key}/resources`); } catch (e) { $("map-legend").textContent = e.message; return; }
  if (W.map.key !== key) return;
  if (!r.available) { $("map-legend").textContent = r.reason || "This week's layout is not known."; return; }
  W.resources = r;
  drawResources();
}
function drawResources() {
  const pois = $("map-pois"); pois.replaceChildren();
  const legend = $("map-legend"); legend.replaceChildren();
  const r = W.resources;
  if (!r || W.map.key !== "DeepDesert") return;
  legend.append(el("span", { className: "muted", textContent: `Layout ${r.seed} of 12${r.layout.confidence ? ` (${r.layout.confidence.toLowerCase()})` : ""}:` }));
  for (const l of r.layout.legend || []) legend.append(el("span", { className: "lg" }, el("img", { src: ICON(l.type), alt: "" }), document.createTextNode(` ${l.label} ${l.count}`)));
  if (!$("map-res").checked) return;
  for (const s of r.layout.largeSpiceSectors || []) {
    const ri = DD_ROWS.indexOf(s[0]), col = +s.slice(1);
    if (ri < 0 || !(col >= 1 && col <= 9)) continue;
    pois.append(el("div", { className: "spice-cell", title: `Large spice field — sector ${s}`, style: `left:${(col - 1) / 9 * 100}%;top:${ri / 9 * 100}%` }));
  }
  for (const p of r.layout.pois || []) {
    const ri = DD_ROWS.indexOf(String(p.sector)[0]), col = +String(p.sector).slice(1);
    if (ri < 0 || !(col >= 1 && col <= 9)) continue;
    const sx = Math.min(4, Math.max(1, p.subx)), sy = Math.min(3, Math.max(0, p.suby));
    const label = (r.layout.legend || []).find((l) => l.type === p.type)?.label || p.type;
    pois.append(el("img", { className: "poi", src: ICON(p.type), alt: label, title: `${label} — sector ${p.sector}`,
      style: `left:${((col - 1) + (sx - 0.5) / 4) / 9 * 100}%;top:${(ri + (sy + 0.5) / 4) / 9 * 100}%` }));
  }
}
$("map-res").addEventListener("change", drawResources);

// Click-to-teleport. TeleportTo has no map: it moves a player within the map they are on, so only
// players on this map are offered, and the game snaps them to the nearest safe spot.
function clearPick() { W.pick = null; $("map-pick").hidden = true; $("map-tp").hidden = true; }
$("map-stage").addEventListener("click", (ev) => {
  if (ev.target.closest(".map-dot")) return;
  const rect = $("map-img").getBoundingClientRect();
  const fx = (ev.clientX - rect.left) / rect.width, fy = (ev.clientY - rect.top) / rect.height;
  if (!(fx >= 0 && fx <= 1 && fy >= 0 && fy <= 1)) return;
  const m = W.map;
  W.pick = { x: Math.round(m.minX + fx * (m.maxX - m.minX)), y: Math.round(m.minY + fy * (m.maxY - m.minY)) };
  const pick = $("map-pick"); pick.hidden = false; pick.style.left = `${fx * 100}%`; pick.style.top = `${fy * 100}%`;
  const here = W.markers.filter((mk) => mk.online && mk.fls);
  $("map-tp").hidden = false; $("map-tp-result").textContent = "";
  $("map-tp-where").textContent = `${m.key === "DeepDesert" ? "Sector " + sector({ left: fx * 100, top: fy * 100 }) + " · " : ""}${W.pick.x.toLocaleString()}, ${W.pick.y.toLocaleString()}`;
  $("map-tp-player").replaceChildren(...(here.length ? here.map((mk) => el("option", { value: mk.fls, textContent: mk.name }))
    : [el("option", { value: "", textContent: "nobody online on this map" })]));
  $("map-tp-go").disabled = !here.length;
});
$("map-tp-cancel").addEventListener("click", clearPick);
$("map-tp-go").addEventListener("click", async () => {
  const fls = $("map-tp-player").value, who = $("map-tp-player").selectedOptions[0]?.textContent;
  const mk = W.markers.find((x) => x.fls === fls);
  if (!fls || !W.pick || !mk) return;
  const out = $("map-tp-result"); out.className = "result"; out.textContent = "Teleporting…";
  try {
    const r = await api(`/api/players/${fls}/teleport`, { x: W.pick.x, y: W.pick.y, z: Math.round(mk.z) + 3000 });
    out.className = `result ${r.ok ? "ok" : "bad"}`; out.textContent = r.ok ? `${who} teleported. ${r.output}` : r.output;
    if (r.ok) { clearPick(); setTimeout(refreshMarkers, 4000); }
  } catch (e) { out.className = "result bad"; out.textContent = e.message; }
});
async function refreshMarkers() {
  const key = W.map.key;
  try { const r = await api(`/api/map/${key}`); if (W.map.key === key) { W.markers = r.markers; $("map-error").textContent = ""; } }
  catch (e) { $("map-error").textContent = e.message; }
  drawMarkers();
}
function drawMarkers() {
  const showOff = $("map-offline").checked;
  const list = W.markers.filter((m) => showOff || m.online);
  const online = W.markers.filter((m) => m.online).length;
  $("map-count").textContent = `${online} online here`;
  const dots = $("map-dots"); dots.replaceChildren();
  const items = $("map-list"); items.replaceChildren();
  for (const m of list) {
    const p = project(m.x, m.y, W.map);
    const where = W.map.key === "DeepDesert" ? ` · sector ${sector(p)}` : "";
    const dot = el("button", { type: "button", className: `map-dot${m.online ? " on" : ""}`, title: `${m.name}${where}` });
    dot.style.left = `${p.left}%`; dot.style.top = `${p.top}%`;
    const label = el("span", { className: "map-label", textContent: m.name });
    dot.append(label);
    const open = () => { if (m.fls) { openTab("players"); openPlayer(m.fls); } };
    dot.addEventListener("click", open);
    dots.append(dot);
    const row = el("li", {}, el("span", { className: `mark ${m.online ? "ok" : ""}`, textContent: "●" }),
      el("button", { type: "button", className: "linklike", textContent: m.name }),
      el("span", { className: "muted", textContent: `${m.online ? "online" : "offline"}${where}${m.partition ? " · partition " + m.partition : ""}` }));
    row.querySelector("button").addEventListener("click", open);
    items.append(row);
  }
  if (!list.length) items.append(el("li", { className: "muted", textContent: "Nobody on this map right now." }));
}
$("map-offline").addEventListener("change", drawMarkers);

// ---- bases -----------------------------------------------------------------
async function loadBases() {
  $("bases-error").textContent = "";
  try { const r = await api("/api/bases"); W.bases = r.bases; }
  catch (e) { $("bases-error").textContent = e.message; W.bases = []; }
  renderBases();
}
function renderBases() {
  const q = $("base-search").value.trim().toLowerCase();
  const rows = W.bases.filter((b) => !q || `${b.owner} ${b.map} ${b.base_id}`.toLowerCase().includes(q));
  $("base-count").textContent = `${W.bases.length} bases`;
  $("base-list").replaceChildren(...rows.map((b) => {
    const btn = el("button", { type: "button" }, el("span", { className: "dot on" }),
      el("span", { className: "pname", textContent: b.owner || `Base #${b.base_id}` }),
      el("span", { className: "psub", textContent: `${b.map} · ${b.pieces} pieces · ${b.placeables} placeables` }));
    btn.setAttribute("aria-current", String(W.base === b.base_id));
    btn.addEventListener("click", () => openBase(b.base_id));
    return el("li", {}, btn);
  }));
  if (!rows.length) $("base-list").append(el("li", { className: "muted", textContent: W.bases.length ? "No match." : "No bases yet." }));
}
$("base-search").addEventListener("input", renderBases);
$("bases-refresh").addEventListener("click", () => { loadBases(); if (W.base) openBase(W.base, true); });
async function openBase(id, keep = false) {
  W.base = id; renderBases();
  const b = W.bases.find((x) => x.base_id === id) || {};
  $("base-empty").hidden = true; $("base-detail").hidden = false;
  $("b-title").textContent = b.owner ? `${b.owner}'s base` : `Base #${id}`;
  $("b-sub").textContent = `#${id} · ${b.map || ""}`;
  if (!keep) $("b-result").textContent = "";
  let d;
  try { d = await api(`/api/bases/${id}`); } catch (e) { showResult("b-result", { ok: false, error: e.message }); return; }
  if (d.queued.length && !keep) showResult("b-result", { ok: true, output: `Queued for the next restart: ${d.queued.join(" and ")} refill.` });
  const num = (v) => (v === "" || v === undefined ? "–" : Number(v).toLocaleString());
  const water = d.water.map((r) => ({ ...r, name: r.water_type || r.type || r.placeable_id }));
  $("b-water").replaceChildren(...table(water, [["name", "Device"], ["stored", "Stored", num], ["capacity", "Capacity", num]]));
  const fuel = d.fuel.map((r) => ({ ...r, name: r.generator || r.placeable_id }));
  const fuelCols = [["name", "Generator"], ["fuel", "Fuel"]];
  for (const k of Object.keys(d.fuel[0] || {})) if (!["placeable_id", "generator", "fuel", "name"].includes(k)) fuelCols.push([k, k.replace(/_/g, " ")]);
  $("b-fuel").replaceChildren(...table(fuel, fuelCols));
  if (d.errors.length) showResult("b-result", { ok: false, error: d.errors.join("\n") });
}
document.querySelectorAll("[data-refill]").forEach((b) => b.addEventListener("click", async () => {
  if (!W.base) return;
  await post(`/api/bases/${W.base}/refill`, { kind: b.dataset.refill }, "b-result");
  openBase(W.base, true);
}));

// ---- guilds ----------------------------------------------------------------
async function loadGuilds() {
  $("guilds-error").textContent = "";
  try { W.guilds = (await api("/api/guilds")).guilds; } catch (e) { $("guilds-error").textContent = e.message; W.guilds = []; }
  renderGuilds();
}
function renderGuilds() {
  const q = $("guild-search").value.trim().toLowerCase();
  const rows = W.guilds.filter((g) => !q || `${g.name} ${g.faction}`.toLowerCase().includes(q));
  $("guild-count").textContent = `${W.guilds.length} guilds`;
  $("guild-list").replaceChildren(...rows.map((g) => {
    const btn = el("button", { type: "button" }, el("span", { className: "dot" }),
      el("span", { className: "pname", textContent: g.name || g.guild_name || `Guild #${g.guild_id}` }),
      el("span", { className: "psub", textContent: `${g.faction} · ${g.members} members` }));
    btn.setAttribute("aria-current", String(W.guild === g.guild_id));
    btn.addEventListener("click", () => openGuild(g.guild_id));
    return el("li", {}, btn);
  }));
  if (!rows.length) $("guild-list").append(el("li", { className: "muted", textContent: W.guilds.length ? "No match." : "No guilds yet." }));
}
$("guild-search").addEventListener("input", renderGuilds);
async function openGuild(id, keep = false) {
  W.guild = id; renderGuilds();
  const g = W.guilds.find((x) => x.guild_id === id) || {};
  $("guild-empty").hidden = true; $("guild-detail").hidden = false;
  $("g-name").textContent = g.name || g.guild_name || `Guild #${id}`;
  $("g-sub").textContent = `${g.faction || ""} · ${g.members || 0} members`;
  $("g-desc").value = g.description || "";
  if (!keep) $("g-result").textContent = "";
  let d;
  try { d = await api(`/api/guilds/${id}`); } catch (e) { showResult("g-result", { ok: false, error: e.message }); return; }
  const body = $("g-members"); body.replaceChildren();
  for (const m of d.members) {
    const leader = m.role === "leader";
    const role = el("button", { className: "ghost small", textContent: leader ? "Make member" : "Make leader" });
    role.addEventListener("click", async () => {
      if (!confirm(leader ? `Demote ${m.character}?` : `Make ${m.character} the leader? The current leader becomes a member.`)) return;
      await post(`/api/guilds/${id}/set-role`, { player_id: m.player_id, role: leader ? "50" : "100" }, "g-result");
      openGuild(id, true);
    });
    const kick = el("button", { className: "ghost small danger", textContent: "Remove" });
    kick.disabled = leader; kick.title = leader ? "Make someone else leader first" : "";
    kick.addEventListener("click", async () => {
      if (!confirm(`Remove ${m.character} from the guild?`)) return;
      await post(`/api/guilds/${id}/kick`, { player_id: m.player_id }, "g-result");
      openGuild(id, true);
    });
    body.append(el("tr", {}, el("td", { textContent: m.character || m.player_id }), el("td", { textContent: m.role }),
      el("td", { textContent: m.online_status === "Online" ? "online" : "offline" }), el("td", {}, role, kick)));
  }
  if (!d.members.length) body.append(el("tr", {}, el("td", { className: "muted", textContent: "No members." })));
  $("g-invites").replaceChildren(...(d.invites.length ? d.invites.map((i) => el("li", { textContent: `${i.invitee || i.invitee_id} — invited by ${i.sender || i.sender_id}` }))
    : [el("li", { className: "muted", textContent: "None." })]));
}
$("g-desc-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  await post(`/api/guilds/${W.guild}/describe`, { description: $("g-desc").value }, "g-result");
  loadGuilds();
});
$("g-rename").addEventListener("click", async () => {
  const name = prompt("New guild name (shown in game after the next restart):", $("g-name").textContent);
  if (!name) return;
  await post(`/api/guilds/${W.guild}/rename`, { name }, "g-result");
  loadGuilds();
});
$("g-disband").addEventListener("click", async () => {
  const name = $("g-name").textContent;
  const typed = prompt(`Disband "${name}"? Every member leaves the guild. This cannot be undone.\nType the guild name to confirm:`);
  if (typed === null) return;
  if (typed.trim() !== name) { alert("The name didn't match — nothing was changed."); return; }
  const r = await post(`/api/guilds/${W.guild}/disband`, { confirm: true }, "g-result");
  if (r && r.ok) { W.guild = null; $("guild-detail").hidden = true; $("guild-empty").hidden = false; loadGuilds(); }
});
$("guild-new").addEventListener("click", async () => {
  if (!P.list.length) await loadPlayers();
  const leaderName = prompt("Leader (player name):");
  if (!leaderName) return;
  const leader = P.list.find((p) => p.name.toLowerCase() === leaderName.trim().toLowerCase());
  if (!leader) { alert(`No player called "${leaderName}".`); return; }
  const name = prompt("Guild name:");
  if (!name) return;
  $("guild-empty").hidden = true; $("guild-detail").hidden = false;
  await post("/api/guilds", { leader: leader.fls_id, name }, "g-result");
  loadGuilds();
});

// ---- schedule --------------------------------------------------------------
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
function dayPicker(days) {
  const box = el("div", { className: "days", role: "group", ariaLabel: "Days (none = every day)" });
  for (const d of DAYS) {
    const cb = el("input", { type: "checkbox", value: d, checked: days.includes(d) });
    box.append(el("label", { className: "day" }, cb, document.createTextNode(d[0].toUpperCase() + d.slice(1))));
  }
  return box;
}
const pickedDays = (row) => [...row.querySelectorAll(".days input:checked")].map((c) => c.value);
function restartRow(r = { time: "04:00", days: [], warn_minutes: 10, update: true, reason: "Nightly restart" }) {
  const warn = el("select", { ariaLabel: "Countdown" }, ...[0, 1, 5, 10, 15, 30].map((m) => el("option", { value: m, textContent: m ? `${m} min countdown` : "no countdown" })));
  warn.value = String(r.warn_minutes);
  const rm = el("button", { type: "button", className: "ghost small danger", textContent: "Remove" });
  const row = el("div", { className: "sched-row" },
    el("input", { type: "time", value: r.time, required: true, ariaLabel: "Time" }), dayPicker(r.days), warn,
    el("label", {}, el("input", { type: "checkbox", className: "upd", checked: r.update }), document.createTextNode(" also install game updates")),
    el("input", { className: "reason", value: r.reason, maxLength: 80, placeholder: "Reason players see", ariaLabel: "Reason" }), rm);
  rm.addEventListener("click", () => row.remove());
  return row;
}
function announceRow(a = { time: "20:00", days: [], message: "" }) {
  const rm = el("button", { type: "button", className: "ghost small danger", textContent: "Remove" });
  const row = el("div", { className: "sched-row" },
    el("input", { type: "time", value: a.time, required: true, ariaLabel: "Time" }), dayPicker(a.days),
    el("input", { className: "reason wide", value: a.message, maxLength: 200, placeholder: "Message to everyone in game", ariaLabel: "Message" }), rm);
  rm.addEventListener("click", () => row.remove());
  return row;
}
async function loadSchedule() {
  let r;
  try { r = await api("/api/schedule"); } catch (e) { $("s-result").textContent = e.message; return; }
  $("s-now").textContent = `${r.server_time} (${r.server_day}${r.timezone ? ", " + r.timezone : ""})`;
  $("s-restarts").replaceChildren(...r.schedule.restarts.map(restartRow));
  $("s-announce").replaceChildren(...r.schedule.announcements.map(announceRow));
  $("s-backup").value = String(r.schedule.backup_every_hours);
  if (!$("s-backup").value) $("s-backup").append(el("option", { value: r.schedule.backup_every_hours, textContent: `${r.schedule.backup_every_hours} hours`, selected: true }));
}
$("s-add-restart").addEventListener("click", () => $("s-restarts").append(restartRow()));
$("s-add-announce").addEventListener("click", () => $("s-announce").append(announceRow()));
$("s-save").addEventListener("click", async () => {
  const schedule = {
    restarts: [...$("s-restarts").children].map((row) => ({
      time: row.querySelector("input[type=time]").value, days: pickedDays(row),
      warn_minutes: +row.querySelector("select").value, update: row.querySelector(".upd").checked,
      reason: row.querySelector(".reason").value })),
    announcements: [...$("s-announce").children].map((row) => ({
      time: row.querySelector("input[type=time]").value, days: pickedDays(row), message: row.querySelector(".reason").value })),
    backup_every_hours: +$("s-backup").value,
  };
  try { await api("/api/schedule", { schedule }); $("s-result").textContent = "Saved."; await loadSchedule(); }
  catch (e) { $("s-result").textContent = e.message; }
});

// ---- commands --------------------------------------------------------------
async function loadOps() {
  if (!W.ops.length) {
    try { W.ops = (await api("/api/ops")).ops; } catch (e) { $("op-list").replaceChildren(el("li", { className: "error", textContent: e.message })); return; }
  }
  renderOps();
  if (!P.list.length) await loadPlayers().catch(() => {});
  $("player-options").replaceChildren(...P.list.map((p) => el("option", { value: p.fls_id, label: p.name })));
}
function renderOps() {
  const q = $("op-filter").value.trim().toLowerCase();
  $("op-list").replaceChildren(...W.ops.filter((o) => !q || (o.op + " " + o.desc).toLowerCase().includes(q)).map((o) => {
    const b = el("button", { type: "button" }, el("strong", { textContent: o.op }), el("span", { className: "muted", textContent: o.desc }));
    if (o.danger) b.classList.add("danger-op");
    b.setAttribute("aria-current", String(W.op === o));
    b.addEventListener("click", () => openOp(o));
    return el("li", {}, b);
  }));
}
$("op-filter").addEventListener("input", renderOps);
function openOp(o) {
  W.op = o; renderOps();
  $("op-form").hidden = false; $("op-out").hidden = true;
  $("op-name").textContent = o.op; $("op-desc").textContent = o.desc;
  $("op-fields").replaceChildren(...o.args.map((a) => {
    const isPlayer = /^(player|player_id)$/.test(a.name);
    const input = el("input", { name: a.name, required: !a.optional, placeholder: a.optional ? `optional${a.default ? " — default " + a.default : ""}` : "" });
    if (isPlayer) { input.setAttribute("list", "player-options"); input.placeholder = "player — pick from the list"; }
    const choices = a.name.includes("|") ? a.name.split("|") : null;
    const field = choices ? el("select", { name: a.name }, ...(a.optional ? [el("option", { value: "", textContent: "(default)" })] : []),
      ...choices.map((c) => el("option", { value: c, textContent: c }))) : input;
    return el("label", { className: "stack" }, document.createTextNode(isPlayer ? "player" : a.name), field);
  }));
  if (!o.args.length) $("op-fields").append(el("p", { className: "muted", textContent: "No values needed." }));
}
$("op-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const o = W.op;
  const args = [...$("op-fields").querySelectorAll("input, select")].map((f) => f.value.trim());
  if (o.danger) {
    const typed = prompt(`${o.op}: ${o.desc}\n\nType the operation name to run it:`);
    if (typed === null || typed.trim() !== o.op) return;
  }
  const out = $("op-out"); out.hidden = false; out.textContent = "Running…";
  try { const r = await api("/api/ops", { op: o.op, args }); out.textContent = r.output; out.classList.toggle("bad", !r.ok); }
  catch (e) { out.textContent = e.message; out.classList.add("bad"); }
});

window.WORLD = { map: loadMapPage, bases: loadBases, guilds: loadGuilds, schedule: loadSchedule, commands: loadOps,
                 kits: () => window.WORLD_PLAYERS.kits() };

// ---- saved locations (map page) ---------------------------------------------
W.locations = [];
async function loadLocations() {
  try { W.locations = (await api("/api/locations")).locations; } catch (_) { W.locations = []; }
  renderLocations();
}
function onlineHere() { return W.markers.filter((m) => m.online && m.fls); }
function renderLocations() {
  if (!W.map) return;
  const here = onlineHere();
  const opts = () => here.length ? here.map((m) => el("option", { value: m.fls, textContent: m.name }))
    : [el("option", { value: "", textContent: "nobody online here" })];
  const list = $("loc-list"); list.replaceChildren();
  const mine = W.locations.filter((l) => l.map === W.map.key);
  for (const l of mine) {
    const who = el("select", { ariaLabel: `Player to send to ${l.name}` }, ...opts());
    const go = el("button", { type: "button", className: "primary small", textContent: "Teleport", disabled: !here.length });
    const del = el("button", { type: "button", className: "ghost small danger", textContent: "Delete" });
    go.addEventListener("click", async () => {
      if (!who.value) return;
      $("loc-result").className = "result"; $("loc-result").textContent = "Teleporting…";
      try {
        const r = await api(`/api/players/${who.value}/goto`, { location: l.name });
        $("loc-result").className = `result ${r.ok ? "ok" : "bad"}`;
        $("loc-result").textContent = r.ok ? `${who.selectedOptions[0].textContent} sent to ${l.name}.` : r.output;
        if (r.ok) setTimeout(refreshMarkers, 4000);
      } catch (e) { $("loc-result").className = "result bad"; $("loc-result").textContent = e.message; }
    });
    del.addEventListener("click", async () => {
      if (!confirm(`Delete the location "${l.name}"?`)) return;
      W.locations = (await api("/api/locations/delete", { name: l.name })).locations; renderLocations();
    });
    list.append(el("li", { className: "loc-row" }, el("strong", { textContent: l.name }), who, go, del));
  }
  if (!mine.length) list.append(el("li", { className: "muted", textContent: "None yet — click the map and choose Save this spot, or save where a player stands." }));
  const keep = $("loc-player").value;
  $("loc-player").replaceChildren(...opts());
  if ([...$("loc-player").options].some((o) => o.value === keep)) $("loc-player").value = keep;
}
async function saveLocation(body) {
  $("loc-result").className = "result"; $("loc-result").textContent = "Saving…";
  try {
    W.locations = (await api("/api/locations", body)).locations; renderLocations();
    $("loc-result").className = "result ok"; $("loc-result").textContent = `Saved "${body.name}".`;
  } catch (e) { $("loc-result").className = "result bad"; $("loc-result").textContent = e.message; }
}
$("loc-from-player").addEventListener("submit", (ev) => {
  ev.preventDefault();
  if (!$("loc-player").value) return;
  saveLocation({ name: $("loc-name").value.trim(), from_player: $("loc-player").value });
  $("loc-name").value = "";
});
$("map-save-spot").addEventListener("click", () => {
  if (!W.pick) return;
  const name = prompt("Name for this spot:");
  if (!name || !name.trim()) return;
  // The click has no height: start above the highest player seen here; teleports snap to safe ground.
  const z = Math.max(0, ...W.markers.map((m) => m.z)) + 3000;
  saveLocation({ name: name.trim(), map: W.map.key, x: W.pick.x, y: W.pick.y, z: Math.round(z) });
  clearPick();
});
const _drawMarkers = drawMarkers;
drawMarkers = function () { _drawMarkers(); renderLocations(); };  // player lists follow the dots
const _loadMapPage = loadMapPage;
WORLD.map = async function () { await _loadMapPage(); loadLocations(); };

// ---- server information (Commands page): answers shown here, not in the console ----
function ago2(ts) { return typeof ago === "function" ? ago(ts) : ts; }
const INFO = {
  status: (r) => {
    const rows = [["State", r.state], ["Version", r.version], ["Players online", r.players ?? "–"],
      ["Players can join", r.state === "running" ? (r.joinable ? "yes" : "maps still loading") : "no"],
      ["Up for", r.uptime ? duration(r.uptime) : "–"],
      ["Pending", r.pending ? `${r.pending.kind} in ${duration(r.pending.in)}${r.pending.reason ? " — " + r.pending.reason : ""}` : "nothing"],
      ["Backups", `${(r.backups || []).length}${r.backups && r.backups[0] ? " (newest " + r.backups[0] + ")" : ""}`]];
    const dl = el("dl", { className: "facts" }, ...rows.map(([k, v]) => el("div", {}, el("dt", { textContent: k }), el("dd", { textContent: String(v) }))));
    const svc = el("ul", { className: "list services" }, ...Object.entries(r.services || {}).map(([n, up]) => li(up ? "ok" : "bad", up ? "●" : "○", n)));
    return [dl, el("h3", { textContent: "Services" }), svc];
  },
  health: (r) => [el("ul", { className: "list" }, ...r.checks.map((c) => {
    const [cls, mark] = c.status === "ok" ? ["ok", "✔"] : c.status === "warn" ? ["warn", "!"] : ["bad", "✘"];
    return li(cls, mark, `${c.name}: ${c.message}`, c.status !== "ok" ? c.fix : "");
  }))],
  ports: (r) => [el("ul", { className: "list" }, ...r.lines.map((l) => el("li", { textContent: l })),
    ...(r.problems || []).map((p) => li("bad", "✘", p)))],
  config: (r) => [r.problems.length
    ? el("ul", { className: "list" }, ...r.problems.map((p) => li("bad", "✘", p)))
    : el("p", { className: "result ok", textContent: "config/dune.toml and the Startup tab settings are valid." })],
  players: (r) => {
    if (!r.players.length) return [el("p", { className: "muted", textContent: "No players yet." })];
    const rows = r.players.map((p) => {
      const open = el("button", { className: "ghost small", textContent: "Open" });
      open.addEventListener("click", () => { openTab("players"); openPlayer(p.fls_id); });
      return el("tr", {}, el("td", { textContent: p.name }), el("td", { textContent: p.online ? "online" : "offline" }),
        el("td", { textContent: p.online ? "now" : ago2(p.last_activity) }), el("td", { textContent: p.banned ? "banned" : "" }), el("td", {}, open));
    });
    const online = r.players.filter((p) => p.online).length;
    return [el("p", { className: "muted", textContent: `${online} online · ${r.players.length} total` }),
      el("div", { className: "table-wrap" }, el("table", { className: "inv" },
        el("thead", {}, el("tr", {}, ...["Name", "Status", "Last seen", "", ""].map((h) => el("th", { textContent: h })))),
        el("tbody", {}, ...rows)))];
  },
};
document.querySelectorAll("[data-info]").forEach((b) => b.addEventListener("click", async () => {
  const what = b.dataset.info;
  document.querySelectorAll("[data-info]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  const out = $("info-out");
  out.replaceChildren(el("p", { className: "muted", textContent: what === "health" ? "Running the health check…" : "Loading…" }));
  try {
    const r = await api(what === "players" ? "/api/players" : `/api/info/${what}`);
    out.replaceChildren(...INFO[what](r));
  } catch (e) { out.replaceChildren(el("p", { className: "result bad", textContent: e.message })); }
}));
async function serverAction(line, done) {
  $("srv-result").className = "result"; $("srv-result").textContent = "Working…";
  try { await api("/api/command", { line }); $("srv-result").className = "result ok"; $("srv-result").textContent = done; }
  catch (e) { $("srv-result").className = "result bad"; $("srv-result").textContent = e.message; }
}
$("srv-backup").addEventListener("click", () => serverAction("backup", "Backup started — it appears on the Backups page when it finishes (usually under a minute)."));
$("srv-cancel").addEventListener("click", () => serverAction("cancel", "Cancelled any pending restart or update."));

// Console verbosity is a switch on the Console page.
$("verbose").addEventListener("change", async () => {
  try { await api("/api/verbose", { on: $("verbose").checked }); } catch (_) { $("verbose").checked = !$("verbose").checked; }
});
WORLD.console = async () => { try { $("verbose").checked = (await api("/api/info/verbose")).verbose; } catch (_) {} };
WORLD.commands = (orig => async () => { await orig(); if (!$("info-out").childElementCount) document.querySelector('[data-info="status"]').click(); })(WORLD.commands);

// ---- instances: start / stop maps -------------------------------------------
const GiB = 1024 ** 3;
let instTimer = null;
const stateLabel = { running: "running", starting: "starting…", stopped: "stopped", failing: "failing (retrying)" };
async function loadInstances() {
  let r;
  try { r = await api("/api/instances"); }
  catch (e) { $("inst-body").replaceChildren(el("tr", {}, el("td", { className: "error", textContent: e.message }))); return; }
  const mem = r.memory;
  const pct = mem ? Math.min(100, r.game_ram / mem * 100) : 0;
  $("ram-fill").style.width = `${pct}%`;
  $("ram-fill").className = pct > 90 ? "bad" : pct > 75 ? "warn" : "";
  $("ram-text").textContent = mem ? `Game servers use ${(r.game_ram / GiB).toFixed(1)} GiB of ${(mem / GiB).toFixed(0)} GiB`
    : `Game servers use ${(r.game_ram / GiB).toFixed(1)} GiB`;
  $("inst-note").textContent = r.stand_in ? "" : "The Kubernetes stand-in is not answering, so maps can't be started or stopped right now.";
  const body = $("inst-body"); body.replaceChildren();
  for (const i of r.instances) {
    const btn = el("button", { className: `small ${i.state === "stopped" ? "primary" : "danger"}` });
    btn.textContent = i.state === "stopped" ? "Start" : "Stop";
    btn.disabled = i.locked;
    if (i.locked) btn.title = "The main sietch stops with the server — use Restart on the Overview page";
    btn.addEventListener("click", () => instanceAct(i, i.state === "stopped" ? "start" : "stop", false));
    const since = i.since ? duration(Math.max(0, Math.round(Date.now() / 1000 - i.since))) : "–";
    body.append(el("tr", { className: i.state === "stopped" ? "dim" : "" },
      el("td", {}, el("strong", { textContent: i.name }), i.parked ? el("span", { className: "tag", textContent: "kept stopped" }) : null,
        i.kind === "copy" ? el("span", { className: "tag", textContent: "copy" }) : null),
      el("td", {}, el("span", { className: `mark ${i.state === "running" ? "ok" : i.state === "stopped" ? "" : "warn"}`, textContent: "● " }),
        document.createTextNode(stateLabel[i.state] || i.state)),
      el("td", { textContent: i.state === "stopped" ? "–" : String(i.players) }),
      el("td", { textContent: i.ram ? `${(i.ram / GiB).toFixed(1)} GiB` : "–" }),
      el("td", { textContent: i.state === "stopped" ? "–" : since }),
      el("td", {}, btn)));
  }
  if (!r.instances.length) body.append(el("tr", {}, el("td", { className: "muted", textContent: "No instances yet." })));
}
async function instanceAct(i, action, force) {
  if (!force && action === "stop" && !confirm(`Stop ${i.name}? Its game server shuts down and frees its RAM.`)) return;
  const out = $("inst-result"); out.className = "result"; out.textContent = `${action === "stop" ? "Stopping" : "Starting"} ${i.name}…`;
  try {
    const r = await api("/api/instances", { partition: i.partition, action, force });
    if (r.confirm) {
      if (confirm(`${r.output}\n\nStop it anyway?`)) return instanceAct(i, action, true);
      out.textContent = "Cancelled."; return;
    }
    out.className = `result ${r.ok ? "ok" : "bad"}`; out.textContent = r.output;
  } catch (e) { out.className = "result bad"; out.textContent = e.message; }
  setTimeout(loadInstances, 3000);
}
$("inst-refresh").addEventListener("click", loadInstances);
WORLD.instances = () => {
  loadInstances();
  clearInterval(instTimer);
  instTimer = setInterval(() => { if (!$("tab-instances").hidden) loadInstances(); }, 10000);
};
