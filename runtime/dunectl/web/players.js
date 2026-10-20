"use strict";
// Players page: list, detail, actions, kits. Uses $ and api() from app.js.

const P = { list: [], current: null, kits: [], pick: null, loaded: false };

function el(tag, props = {}, ...kids) {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...kids.filter((k) => k !== null && k !== undefined));
  return e;
}
function ago(ts) {
  if (!ts) return "never";
  const t = Date.parse(ts.replace(" ", "T").replace(/(\+\d\d)$/, "$1:00"));
  if (Number.isNaN(t)) return ts;
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}
// Item picture, served (and cached) by the panel; hidden when the wiki has none.
function itemImg(id, cls = "icon") {
  const img = el("img", { className: cls, src: `/api/item-image/${encodeURIComponent(id)}`, alt: "", loading: "lazy" });
  let tries = 0;  // the panel fetches a new picture in the background and answers "not yet" meanwhile
  img.addEventListener("error", () => {
    if (++tries > 5) { img.classList.add("noimg"); return; }
    setTimeout(() => { img.src = `/api/item-image/${encodeURIComponent(id)}#${tries}`; }, 2500 * tries);
  });
  return img;
}
const fmt = (n) => (n === null || n === undefined || n === "") ? "–" : Number(n).toLocaleString();

// ---- list ------------------------------------------------------------------
async function loadPlayers() {
  $("players-error").textContent = "";
  try { P.list = (await api("/api/players")).players; }
  catch (e) { $("players-error").textContent = e.message; P.list = []; }
  renderList();
  if (!P.loaded) { P.loaded = true; loadKits(); }
}
window.loadPlayers = loadPlayers;

function renderList() {
  const q = $("player-search").value.trim().toLowerCase();
  const onlineOnly = $("online-only").checked;
  const rows = P.list.filter((p) => (!onlineOnly || p.online) &&
    (!q || p.name.toLowerCase().includes(q) || p.steam_id.includes(q) || p.fls_id.toLowerCase().includes(q)));
  const online = P.list.filter((p) => p.online).length;
  $("player-count").textContent = `${online} online · ${P.list.length} total`;
  const ul = $("player-list"); ul.replaceChildren();
  for (const p of rows) {
    const b = el("button", { type: "button" },
      el("span", { className: `dot${p.online ? " on" : ""}`, title: p.online ? "online" : "offline" }),
      el("span", { className: "pname", textContent: p.name }),
      el("span", { className: "psub" },
        p.banned ? el("span", { className: "tagban", textContent: "banned · " }) : null,
        document.createTextNode(p.online ? "online now" : `seen ${ago(p.last_activity)}`)));
    b.setAttribute("aria-current", String(P.current && P.current.fls_id === p.fls_id));
    b.addEventListener("click", () => openPlayer(p.fls_id));
    ul.append(el("li", {}, b));
  }
  if (!rows.length) ul.append(el("li", { className: "muted", textContent: P.list.length ? "No match." : "No players yet." }));
}
$("player-search").addEventListener("input", renderList);
$("online-only").addEventListener("change", renderList);
$("players-refresh").addEventListener("click", () => { loadPlayers(); if (P.current) openPlayer(P.current.fls_id); });

// ---- detail ----------------------------------------------------------------
async function openPlayer(fls, keepResult = false) {
  const listed = P.list.find((p) => p.fls_id === fls);
  $("player-empty").hidden = true; $("player-detail").hidden = false;
  $("p-name").textContent = listed ? listed.name : fls;
  if (!keepResult) { $("p-result").textContent = "Loading…"; $("p-result").className = "result"; }
  let d;
  try { d = await api(`/api/players/${fls}`); }
  catch (e) { $("p-result").textContent = e.message; $("p-result").className = "result bad"; return; }
  P.current = Object.assign(d, { name: listed ? listed.name : fls });
  renderList();
  if (!keepResult) $("p-result").textContent = "";
  $("p-online").textContent = d.online ? "online" : "offline";
  $("p-online").className = `badge${d.online ? " online" : ""}`;
  $("p-banned").hidden = !d.ban;
  $("p-ban").hidden = !!d.ban; $("p-unban").hidden = !d.ban;
  if (d.ban) $("p-banned").title = `${d.ban.reason || "no reason"} — ${d.ban.expires_at ? "until " + d.ban.expires_at : "permanent"}`;
  $("p-map").textContent = d.map || "–";
  $("p-life").textContent = d.life || "–";
  $("p-last").textContent = d.online ? "now" : ago(d.last_activity);
  $("p-steam").textContent = d.steam_id || "–";
  $("p-fls").textContent = d.fls_id;
  const s = d.summary;
  $("p-level").textContent = s ? `${s.xp.level}${s.xp.atCap ? " (max)" : ""} · ${fmt(s.xp.xp)} XP` : "–";
  $("p-solaris").textContent = s ? fmt(s.solaris) : "–";
  $("p-align").textContent = s ? (s.alignment || "none") : "–";
  $("p-rep1").textContent = s ? `${fmt(s.houses[0].rep)} · ${s.houses[0].tier}` : "–";
  $("p-rep2").textContent = s ? `${fmt(s.houses[1].rep)} · ${s.houses[1].tier}` : "–";
  $("rename").value = "";
  renderInventory(d);
  if (window.matchMedia("(max-width: 760px)").matches) $("player-detail").scrollIntoView({ behavior: "smooth" });
}

function renderInventory(d) {
  $("inv-error").textContent = d.inventory_error || (d.inventory.length ? "" : "Empty.");
  const body = $("inv-body"); body.replaceChildren();
  for (const it of d.inventory) {
    const dur = it.durability === "N/A" ? "–" : `${Math.round(+it.durability)} / ${Math.round(+it.max_durability)}`;
    const edit = el("button", { className: "ghost small", textContent: "Edit" });
    const del = el("button", { className: "ghost small danger", textContent: "Delete" });
    edit.addEventListener("click", () => editItem(it));
    del.addEventListener("click", () => {
      if (confirm(`Delete ${it.qty} × ${it.name} from ${P.current.name}'s inventory? This cannot be undone.`))
        act("item-delete", { item_id: +it.item_id });
    });
    body.append(el("tr", {},
      el("td", { className: "with-icon", title: it.template }, itemImg(it.template), document.createTextNode(it.name)),
      el("td", { textContent: it.qty }),
      el("td", { textContent: it.quality }), el("td", { textContent: dur }),
      el("td", { textContent: it.inventory }), el("td", {}, edit, del)));
  }
}
function editItem(it) {
  const qty = prompt(`New quantity for ${it.name}:`, it.qty);
  if (qty === null) return;
  const quality = prompt(`Quality (0-5) for ${it.name}:`, it.quality);
  if (quality === null) return;
  act("item-edit", { item_id: +it.item_id, stack: +qty, quality: +quality });
}

// ---- actions ---------------------------------------------------------------
async function act(action, body = {}) {
  if (!P.current) return;
  const out = $("p-result");
  out.className = "result"; out.textContent = "Working…";
  try {
    const r = await api(`/api/players/${P.current.fls_id}/${action}`, body);
    out.className = `result ${r.ok ? "ok" : "bad"}`; out.textContent = r.output;
  } catch (e) { out.className = "result bad"; out.textContent = e.message; }
  setTimeout(() => { if (P.current) openPlayer(P.current.fls_id, true); }, 1200);
}
document.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => {
  const a = b.dataset.act;
  const ask = { kick: "Kick", "grant-keystones": "Grant every keystone to", "tech-unlock": b.dataset.mode === "lock-all" ? "Re-lock all recipes for" : "Unlock all recipes for" }[a];
  if (ask && !confirm(`${ask} ${P.current.name}?`)) return;
  act(a, b.dataset.mode ? { mode: b.dataset.mode } : {});
}));
document.querySelectorAll("form[data-amount]").forEach((f) => f.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const amount = +f.querySelector("input").value;
  if (!amount) return;
  act(f.dataset.amount, { amount });
  f.reset();
}));
$("rep-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  act("faction-rep", { faction: $("rep-faction").value, amount: +$("rep-amount").value });
  $("rep-amount").value = "";
});
$("rename-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const name = $("rename").value.trim();
  if (name && confirm(`Rename ${P.current.name} to "${name}"?`)) act("rename", { name });
});
$("p-ban").addEventListener("click", () => {
  const reason = prompt(`Ban ${P.current.name}. Reason (shown in the ban list):`, "");
  if (reason === null) return;
  const hours = prompt("For how many hours? Leave empty for a permanent ban.", "");
  if (hours === null) return;
  act("ban", { name: P.current.name, reason, hours: hours.trim() ? +hours : null });
});
$("p-unban").addEventListener("click", () => { if (confirm(`Lift the ban on ${P.current.name}?`)) act("unban"); });
document.querySelectorAll("[data-wipe]").forEach((b) => b.addEventListener("click", () => {
  const what = b.dataset.wipe === "clean" ? "wipe the whole inventory of" : "reset all XP and skills of";
  const typed = prompt(`This will ${what} ${P.current.name} and cannot be undone.\nType the player's name to confirm:`);
  if (typed !== null && typed.trim() === P.current.name) act(b.dataset.wipe, { confirm: true });
  else if (typed !== null) alert("The name didn't match — nothing was changed.");
}));

// ---- item search (shared by Give and the kit editor) -------------------------
function itemSearch(input, list, onPick) {
  let timer;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      if (q.length < 2) { list.hidden = true; return; }
      const { items } = await api(`/api/items?q=${encodeURIComponent(q)}`).catch(() => ({ items: [] }));
      list.replaceChildren(...items.map((i) => {
        const b = el("button", { type: "button", className: "with-icon" }, itemImg(i.id), document.createTextNode(i.name),
          el("span", { className: "cat", textContent: i.category }));
        b.addEventListener("click", () => { list.hidden = true; onPick(i); });
        return el("li", {}, b);
      }));
      list.hidden = !items.length;
    }, 200);
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") list.hidden = true;
    if (ev.key === "ArrowDown" && !list.hidden) { ev.preventDefault(); list.querySelector("button")?.focus(); }
  });
  document.addEventListener("click", (ev) => { if (!list.contains(ev.target) && ev.target !== input) list.hidden = true; });
}
itemSearch($("give-search"), $("give-results"), (i) => {
  P.pick = i; $("give-search").value = i.name; $("give-btn").disabled = false; $("give-qty").focus();
});
$("give-search").addEventListener("input", () => { P.pick = null; $("give-btn").disabled = true; });
$("give-btn").addEventListener("click", () => {
  if (P.pick) act("give-item", { template: P.pick.id, qty: +$("give-qty").value || 1, quality: +$("give-quality").value });
});

// ---- kits & gifts page -----------------------------------------------------
async function loadKits() {
  try { P.kits = (await api("/api/kits")).kits; } catch (_) { P.kits = []; }
  const opts = () => P.kits.map((k) => el("option", { value: k.name, textContent: `${k.name} (${k.items.length} items)` }));
  $("kit-select").replaceChildren(...opts());
  $("gift-kit").replaceChildren(...opts());
  const wk = $("wel-kit").value;
  $("wel-kit").replaceChildren(...opts());
  if (wk) $("wel-kit").value = wk;
}
$("kit-give").addEventListener("click", () => {
  const kit = $("kit-select").value;
  if (kit && confirm(`Give the kit "${kit}" to ${P.current.name}?`)) act("give-kit", { kit });
});
$("kit-edit").addEventListener("click", () => openTab("kits"));

let draft = [], editing = 0, dirty = false;
function kdRender() {
  $("kd-list").replaceChildren(...draft.map((k, i) => {
    const b = el("button", { type: "button" }, el("span", { className: "dot on" }),
      el("span", { className: "pname", textContent: k.name || "(unnamed)" }),
      el("span", { className: "psub", textContent: `${k.items.length} items` }));
    b.setAttribute("aria-current", String(i === editing));
    b.addEventListener("click", () => { editing = i; kdRender(); });
    return el("li", {}, b);
  }));
  const kit = draft[editing];
  $("kd-name").value = kit ? kit.name : "";
  $("kd-name").disabled = $("kd-search").disabled = $("kd-delete").disabled = !kit;
  const body = $("kd-items"); body.replaceChildren();
  if (!kit) { body.append(el("tr", {}, el("td", { className: "muted", textContent: "No kits — press New kit." }))); return; }
  kit.items.forEach((it, idx) => {
    const qty = el("input", { type: "number", min: 1, max: 10000, value: it.qty, ariaLabel: "Quantity" });
    qty.addEventListener("change", () => { it.qty = +qty.value || 1; dirty = true; });
    const rm = el("button", { className: "ghost small danger", textContent: "Remove" });
    rm.addEventListener("click", () => { kit.items.splice(idx, 1); dirty = true; kdRender(); });
    body.append(el("tr", {}, el("td", { className: "with-icon", title: it.id }, itemImg(it.id), document.createTextNode(it.name || it.id)),
      el("td", {}, qty), el("td", {}, rm)));
  });
  if (!kit.items.length) body.append(el("tr", {}, el("td", { className: "muted", textContent: "Empty — add items below." })));
}
async function openKitsPage() {
  if (!dirty) { await loadKits(); draft = JSON.parse(JSON.stringify(P.kits)); editing = Math.min(editing, Math.max(0, draft.length - 1)); }
  kdRender();
  if (!P.list.length) await loadPlayers();
  $("gift-target").replaceChildren(el("option", { value: "online", textContent: `Everyone online (${P.list.filter((p) => p.online).length})` }),
    ...P.list.map((p) => el("option", { value: p.fls_id, textContent: `${p.name}${p.online ? " — online" : ""}` })));
  loadWelcome();
}
$("kd-name").addEventListener("input", () => { draft[editing].name = $("kd-name").value; dirty = true; kdRender(); $("kd-name").focus(); });
$("kd-new").addEventListener("click", () => { draft.push({ name: "New kit", items: [] }); editing = draft.length - 1; dirty = true; kdRender(); $("kd-name").select(); });
$("kd-delete").addEventListener("click", () => {
  if (draft[editing] && confirm(`Delete the kit "${draft[editing].name}"? (Saved when you press Save kits.)`)) { draft.splice(editing, 1); editing = 0; dirty = true; kdRender(); }
});
itemSearch($("kd-search"), $("kd-results"), (i) => {
  draft[editing].items.push({ id: i.id, name: i.name, qty: 1 }); $("kd-search").value = ""; dirty = true; kdRender();
});
$("kd-cancel").addEventListener("click", () => { dirty = false; openKitsPage(); $("kd-error").textContent = ""; });
$("kd-save").addEventListener("click", async () => {
  const out = $("kd-error"); out.className = "result";
  try {
    await api("/api/kits", { kits: draft.map((k) => ({ name: k.name, items: k.items.map((i) => ({ id: i.id, qty: i.qty })) })) });
    dirty = false; await openKitsPage(); out.className = "result ok"; out.textContent = "Kits saved.";
  } catch (e) { out.className = "result bad"; out.textContent = e.message; }
});

$("gift-btn").addEventListener("click", async () => {
  const target = $("gift-target").value, kit = $("gift-kit").value;
  const who = target === "online" ? "everyone online" : $("gift-target").selectedOptions[0].textContent;
  if (!kit || !confirm(`Give the kit "${kit}" to ${who}?`)) return;
  const out = $("gift-result"); out.className = "result"; out.textContent = "Giving…";
  try { const r = await api("/api/gift", { target, kit }); out.className = `result ${r.ok ? "ok" : "bad"}`; out.textContent = r.output; }
  catch (e) { out.className = "result bad"; out.textContent = e.message; }
});

async function loadWelcome() {
  let w; try { w = await api("/api/welcome"); } catch (_) { return; }
  $("wel-on").checked = w.enabled;
  if (w.kit) $("wel-kit").value = w.kit;
  $("wel-announce").checked = w.announce;
  $("wel-text").value = w.announce_text;
  $("wel-result").className = "result muted";
  $("wel-result").textContent = w.enabled ? `On: "${w.kit}"${w.since ? `, this round since ${w.since}` : ""}.` : "Off.";
}
async function saveWelcome(regrant) {
  const out = $("wel-result"); out.className = "result";
  try {
    await api("/api/welcome", { enabled: $("wel-on").checked, kit: $("wel-kit").value, announce: $("wel-announce").checked,
                                announce_text: $("wel-text").value, regrant });
    await loadWelcome(); out.className = "result ok"; out.textContent = "Saved. " + out.textContent;
  } catch (e) { out.className = "result bad"; out.textContent = e.message; }
}
$("wel-save").addEventListener("click", () => saveWelcome(false));
$("wel-regrant").addEventListener("click", () => {
  if (confirm("Start a new round: every player, including ones who already got it, gets the welcome kit once more. Continue?")) saveWelcome(true);
});
window.WORLD_PLAYERS = { kits: openKitsPage };
