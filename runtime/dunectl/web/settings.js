"use strict";
// Settings page: config/dune.toml as forms. Uses $, api, run, el, loadConfig from the other scripts.

const C = { data: null, changes: {}, page: "world", q: "", cat: "", advanced: false, changedOnly: false };
const GAMEPLAY_FIELDS = ["pvp_partitions", "deep_desert_picker_routing"];

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function pending() { return Object.values(C.changes).reduce((n, kv) => n + Object.keys(kv).length, 0); }
function setChange(section, key, value, original) {
  const kv = C.changes[section] || (C.changes[section] = {});
  if (same(value, original)) delete kv[key]; else kv[key] = value;
  if (!Object.keys(kv).length) delete C.changes[section];
  updateBar();
}
function current(section, key, original) {
  const kv = C.changes[section];
  return kv && key in kv ? kv[key] : original;
}
function updateBar() {
  const n = pending();
  $("set-bar").hidden = n === 0;
  $("set-count").textContent = `${n} unsaved change${n === 1 ? "" : "s"}`;
  document.querySelectorAll("#set-nav button").forEach((b) => {
    b.classList.toggle("dirty", !!C.changes[b.dataset.sec === "gameplay-all" ? "gameplay" : b.dataset.sec]);
  });
}

// ---- controls ----------------------------------------------------------------
function asBool(v) { return v === true || ["true", "1", "yes", "on"].includes(String(v).toLowerCase()); }
function control(kind, value, opts, onValue) {
  const id = `set-${Math.random().toString(36).slice(2)}`;
  let input;
  if (kind === "bool") {
    input = el("input", { type: "checkbox", id, checked: asBool(value), role: "switch" });
    input.addEventListener("change", () => onValue(input.checked));
    return [el("label", { className: "switch" }, input, el("span", { className: "slider", ariaHidden: "true" })), id];
  }
  if (kind === "choice") {
    input = el("select", { id }, ...opts.options.map((o) => el("option", { value: o, textContent: o })));
    input.value = String(value ?? "");
    input.addEventListener("change", () => onValue(input.value));
    return [input, id];
  }
  if (kind === "int" || kind === "float") {
    input = el("input", { type: "number", id, value: value ?? "", step: kind === "int" ? "1" : "any", className: "num" });
    if (opts.min !== undefined) input.min = opts.min;
    if (opts.max !== undefined) input.max = opts.max;
    input.addEventListener("input", () => {
      if (input.value === "" || !input.checkValidity()) return;
      onValue(kind === "int" ? parseInt(input.value, 10) : parseFloat(input.value));
    });
    return [input, id];
  }
  if (kind === "list") {
    input = el("input", { id, value: (value || []).join(", "), placeholder: opts.item === "int" ? "e.g. 8, 101" : "comma-separated" });
    input.addEventListener("input", () => {
      const parts = input.value.split(/[,\s]+/).filter(Boolean);
      onValue(opts.item === "int" ? parts.map(Number).filter((n) => Number.isInteger(n)) : parts);
    });
    return [input, id];
  }
  input = el("input", { id, value: value ?? "", spellcheck: false });
  if (opts.pattern) { input.pattern = opts.pattern; input.placeholder = "e.g. 10m, 90s, 1h"; }
  input.addEventListener("input", () => { if (input.checkValidity()) onValue(input.value); });
  return [input, id];
}

function fieldRow(f) {
  const [ctl, id] = control(f.type, current(f.section, f.key, f.value), f, (v) => { setChange(f.section, f.key, v, f.value); mark(); });
  const reset = el("button", { type: "button", className: "ghost small", textContent: "Default", title: `Default: ${fmtDefault(f.default)}` });
  const row = el("div", { className: "set-row" },
    el("div", { className: "set-label" }, el("label", { htmlFor: id, textContent: f.label }),
      f.help ? el("p", { className: "help", textContent: f.help }) : null,
      f.startup_var && f.startup_value ? el("p", { className: "note", textContent:
        `The Startup tab's ${f.startup_var} is set (${f.startup_value}) and wins over this value.` }) : null),
    el("div", { className: "set-ctl" }, ctl, reset));
  function mark() {
    const v = current(f.section, f.key, f.value);
    row.classList.toggle("changed", f.section in C.changes && f.key in C.changes[f.section]);
    reset.hidden = same(v, f.default);
  }
  reset.addEventListener("click", () => { setChange(f.section, f.key, f.default, f.value); renderPage(); });
  mark();
  return row;
}
function fmtDefault(v) {
  if (Array.isArray(v)) return v.join(", ") || "none";
  if (typeof v === "boolean") return v ? "on" : "off";
  return v === "" || v === undefined || v === null ? "empty" : String(v);
}

// ---- gameplay (the game's own settings) ----------------------------------------
function settingDefault(s) {
  if (s.type === "bool") return asBool(s.default);
  if (s.type === "int") return parseInt(s.default, 10);
  if (s.type === "float") return parseFloat(s.default);
  return s.default;
}
function settingRow(s) {
  const def = settingDefault(s);
  const value = current("gameplay", s.id, s.set ? s.value : def);
  const original = s.set ? s.value : def;
  const [ctl, id] = control(s.type, value, s, (v) => {
    if (!s.set && same(v, def)) setChange("gameplay", s.id, original, original);  // back to default: nothing to write
    else setChange("gameplay", s.id, v, original);
    mark();
  });
  const shown = fmtDefault(Number.isNaN(def) ? s.default : def);  // 2.000000 -> 2, "False" -> off
  const reset = el("button", { type: "button", className: "ghost small", textContent: "Default", title: `Default: ${shown}` });
  const row = el("div", { className: "set-row" },
    el("div", { className: "set-label" }, el("label", { htmlFor: id, textContent: s.label }),
      el("p", { className: "help" }, document.createTextNode(`Default ${shown}`),
        s.range ? document.createTextNode(` · ${s.range}`) : null,
        s.client ? el("span", { className: "tag", textContent: "players need to relog" }) : null,
        s.advanced ? el("span", { className: "tag", textContent: "advanced" }) : null)),
    el("div", { className: "set-ctl" }, ctl, reset));
  function mark() {
    const pendingChange = !!(C.changes.gameplay && s.id in C.changes.gameplay);  // toggle(x, undefined) would flip it
    const effective = pendingChange ? C.changes.gameplay[s.id] : (s.set ? s.value : null);
    row.classList.toggle("changed", pendingChange);
    reset.hidden = effective === null || (pendingChange && effective === null);
  }
  // Default removes the line from the file, so the game's own default applies.
  reset.addEventListener("click", () => { setChange("gameplay", s.id, s.set ? null : original, original); renderPage(); });
  mark();
  return row;
}
function renderGameplay(box) {
  const sec = C.data.sections.find((x) => x.id === "gameplay");
  box.append(el("div", { className: "card" }, el("h2", { textContent: "Gameplay — server" }),
    ...sec.fields.filter((f) => GAMEPLAY_FIELDS.includes(f.key)).map(fieldRow)));
  const cats = [...new Set(C.data.gameplay.map((s) => s.category))].sort();
  const search = el("input", { type: "search", value: C.q, placeholder: "Search 220 game settings…", ariaLabel: "Search settings" });
  const cat = el("select", { ariaLabel: "Category" }, el("option", { value: "", textContent: "All categories" }),
    ...cats.map((c) => el("option", { value: c, textContent: c })));
  cat.value = C.cat;
  const adv = el("input", { type: "checkbox", checked: C.advanced });
  const only = el("input", { type: "checkbox", checked: C.changedOnly });
  const list = el("div", { className: "set-list" });
  const draw = () => {
    const words = C.q.toLowerCase().split(/\s+/).filter(Boolean);
    const hits = C.data.gameplay.filter((s) => (!C.cat || s.category === C.cat) && (C.advanced || !s.advanced || s.set)
      && (!C.changedOnly || s.set || (C.changes.gameplay && s.id in C.changes.gameplay))
      && words.every((w) => `${s.label} ${s.id} ${s.category}`.toLowerCase().includes(w)));
    list.replaceChildren();
    let last = null;
    for (const s of hits.sort((a, b) => a.category.localeCompare(b.category) || a.label.localeCompare(b.label))) {
      if (s.category !== last) { list.append(el("h3", { className: "set-cat", textContent: s.category })); last = s.category; }
      list.append(settingRow(s));
    }
    if (!hits.length) list.append(el("p", { className: "muted", textContent: "No setting matches." }));
  };
  search.addEventListener("input", () => { C.q = search.value; draw(); });
  cat.addEventListener("change", () => { C.cat = cat.value; draw(); });
  adv.addEventListener("change", () => { C.advanced = adv.checked; draw(); });
  only.addEventListener("change", () => { C.changedOnly = only.checked; draw(); });
  box.append(el("div", { className: "card" }, el("h2", { textContent: "Gameplay — game settings" }),
    el("p", { className: "muted small-text", textContent: "Unchanged settings use the game's default. Default removes your change." }),
    el("div", { className: "actions" }, search, cat),
    el("div", { className: "actions" }, el("label", {}, adv, document.createTextNode(" Show advanced settings")),
      el("label", {}, only, document.createTextNode(" Only changed"))),
    list));
  draw();
}

// ---- page ------------------------------------------------------------------------
function renderNav() {
  const items = [...C.data.sections.map((s) => [s.id === "gameplay" ? "gameplay-all" : s.id, s.title]), ["file", "Edit as text"]];
  $("set-nav").replaceChildren(...items.map(([id, title]) => {
    const b = el("button", { type: "button", textContent: title });
    b.dataset.sec = id;
    if (id === C.page) b.setAttribute("aria-current", "page");
    b.addEventListener("click", () => { C.page = id; renderNav(); renderPage(); });
    return b;
  }));
  updateBar();
}
function renderPage() {
  const box = $("set-forms");
  box.replaceChildren();
  $("set-file").hidden = C.page !== "file";
  box.hidden = C.page === "file";
  if (C.page === "file") {
    if (pending()) box.hidden = false, box.append(el("p", { className: "result bad", textContent: "Save or discard your form changes before editing the text." }));
    else if (!configDirty) loadConfig();
    return;
  }
  if (C.page === "gameplay-all") return renderGameplay(box);
  const sec = C.data.sections.find((s) => s.id === C.page);
  box.append(el("div", { className: "card" }, el("h2", { textContent: sec.title }),
    sec.intro ? el("p", { className: "muted", textContent: sec.intro }) : null, ...sec.fields.map(fieldRow)));
}
async function openSettings() {
  try { C.data = await api("/api/config/form"); } catch (e) { $("set-forms").replaceChildren(el("p", { className: "result bad", textContent: e.message })); return; }
  if (C.data.problems.length) {
    $("set-problems").replaceChildren(...C.data.problems.map((p) => el("li", { textContent: p })));
  }
  renderNav(); renderPage();
}
async function saveSettings(thenRestart) {
  const probs = $("set-problems"); probs.replaceChildren();
  try {
    await api("/api/config/form", { changes: C.changes });
    C.changes = {};
    await openSettings();
    $("set-bar").hidden = false;
    $("set-count").textContent = thenRestart ? "Saved — restarting to apply." : "Saved. Restart to apply.";
    if (thenRestart) run("restart");
    setTimeout(updateBar, 4000);
  } catch (e) {
    for (const p of (e.data && e.data.problems) || [e.message]) probs.append(el("li", { textContent: p }));
  }
}
$("set-save").addEventListener("click", () => saveSettings(false));
$("set-save-restart").addEventListener("click", () => { if (confirm("Save and restart the server now (with a countdown if players are on)?")) saveSettings(true); });
$("set-discard").addEventListener("click", () => { C.changes = {}; $("set-problems").replaceChildren(); renderPage(); updateBar(); });
window.addEventListener("beforeunload", (ev) => { if (pending()) ev.preventDefault(); });
// After a text-mode save, the forms show the new values.
$("config-save").addEventListener("click", () => setTimeout(() => C.page === "file" && api("/api/config/form").then((d) => { C.data = d; }), 800));

WORLD.config = openSettings;
