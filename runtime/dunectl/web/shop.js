"use strict";
// Items and Vehicles pages. Uses $, api from app.js and el, itemImg, P, loadPlayers from players.js.

const S = { q: "", cat: "", offset: 0, item: null, vehicles: [], vehicle: null, variant: null, chipsDone: false };

function setResult(id, ok, text) { const o = $(id); o.className = `result ${ok ? "ok" : "bad"}`; o.textContent = text; }
function setImg(img, id) {  // same retry as itemImg(): new pictures arrive a few seconds later
  img.classList.remove("noimg");
  img.dataset.tries = "0";
  img.dataset.id = id;
  img.src = `/api/item-image/${encodeURIComponent(id)}`;
}
for (const img of [$("cg-img"), $("vg-img")]) {
  img.addEventListener("error", () => {
    const n = +img.dataset.tries + 1, id = img.dataset.id;
    img.dataset.tries = String(n);
    if (n > 5) { img.classList.add("noimg"); return; }
    setTimeout(() => { if (img.dataset.id === id) img.src = `/api/item-image/${encodeURIComponent(id)}#${n}`; }, 2500 * n);
  });
}
const CAT_LABEL = (c) => c ? c[0].toUpperCase() + c.slice(1) : "All";

async function playerOptions(select, { everyone = false, onlineOnly = false } = {}) {
  if (!P.list.length) await loadPlayers().catch(() => {});
  const keep = select.value;
  const online = P.list.filter((p) => p.online);
  const people = onlineOnly ? online : P.list;
  select.replaceChildren(
    ...(everyone ? [el("option", { value: "online", textContent: `Everyone online (${online.length})` })] : []),
    ...people.map((p) => el("option", { value: p.fls_id, textContent: `${p.name}${p.online ? " — online" : ""}` })));
  if (!select.options.length) select.append(el("option", { value: "", textContent: onlineOnly ? "nobody online" : "no players" }));
  if ([...select.options].some((o) => o.value === keep)) select.value = keep;
}

// ---- items -----------------------------------------------------------------
async function loadCatalog(append = false) {
  if (!append) S.offset = 0;
  let r;
  try { r = await api(`/api/catalog?q=${encodeURIComponent(S.q)}&cat=${encodeURIComponent(S.cat)}&offset=${S.offset}`); }
  catch (e) { $("cat-count").textContent = e.message; return; }
  if (!S.chipsDone) {
    S.chipsDone = true;
    $("cat-chips").replaceChildren(...[["", null], ...r.categories].map(([c, n]) => {
      const b = el("button", { type: "button", textContent: n ? `${CAT_LABEL(c)} ${n}` : "All" });
      b.dataset.cat = c;
      b.addEventListener("click", () => { S.cat = c; markChips(); loadCatalog(); });
      return b;
    }));
    markChips();
  }
  const grid = $("cat-grid");
  if (!append) grid.replaceChildren();
  for (const i of r.items) {
    const card = el("button", { type: "button", className: "item-card", title: i.id },
      itemImg(i.id, "pic"), el("span", { className: "iname", textContent: i.name }), el("span", { className: "icat", textContent: i.category }));
    card.addEventListener("click", () => pickItem(i, card));
    grid.append(card);
  }
  S.offset = r.offset + r.items.length;
  $("cat-count").textContent = `${r.total.toLocaleString()} item${r.total === 1 ? "" : "s"}`;
  $("cat-more").hidden = S.offset >= r.total;
}
function markChips() {
  document.querySelectorAll("#cat-chips button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.cat === S.cat)));
}
let catTimer;
$("cat-search").addEventListener("input", () => {
  clearTimeout(catTimer);
  catTimer = setTimeout(() => { S.q = $("cat-search").value.trim(); loadCatalog(); }, 250);
});
$("cat-more").addEventListener("click", () => loadCatalog(true));

function pickItem(i, card) {
  S.item = i;
  document.querySelectorAll(".item-card[aria-pressed]").forEach((c) => c.removeAttribute("aria-pressed"));
  card.setAttribute("aria-pressed", "true");
  $("cg-empty").hidden = true; $("cg-body").hidden = false;
  setImg($("cg-img"), i.id);
  $("cg-name").textContent = i.name;
  $("cg-meta").textContent = `${CAT_LABEL(i.category)} · ${i.id}`;
  $("cg-result").textContent = "";
  if (window.matchMedia("(max-width: 900px)").matches) $("cat-give").scrollIntoView({ behavior: "smooth" });
}
$("cg-give").addEventListener("click", async () => {
  if (!S.item) return;
  const target = $("cg-target").value, qty = +$("cg-qty").value || 1, quality = +$("cg-quality").value;
  const who = target === "online" ? "everyone online" : $("cg-target").selectedOptions[0].textContent;
  if (!target || !confirm(`Give ${qty} × ${S.item.name} to ${who}?`)) return;
  $("cg-result").className = "result"; $("cg-result").textContent = "Giving…";
  try {
    const r = target === "online"
      ? await api("/api/gift", { target, template: S.item.id, qty })
      : await api(`/api/players/${target}/give-item`, { template: S.item.id, qty, quality });
    setResult("cg-result", r.ok, r.output);
  } catch (e) { setResult("cg-result", false, e.message); }
});
$("cg-kit").addEventListener("click", async () => {
  if (!S.item) return;
  const kits = (await api("/api/kits")).kits;
  const name = prompt(`Add ${S.item.name} to which kit?\n${kits.map((k) => "• " + k.name).join("\n")}\n(A new name makes a new kit.)`);
  if (!name || !name.trim()) return;
  const qty = +prompt("How many?", "1") || 1;
  let kit = kits.find((k) => k.name.toLowerCase() === name.trim().toLowerCase());
  if (!kit) { kit = { name: name.trim(), items: [] }; kits.push(kit); }
  kit.items.push({ id: S.item.id, qty });
  try {
    await api("/api/kits", { kits: kits.map((k) => ({ name: k.name, items: k.items.map((x) => ({ id: x.id, qty: x.qty })) })) });
    setResult("cg-result", true, `Added to the kit "${kit.name}".`);
  } catch (e) { setResult("cg-result", false, e.message); }
});
async function openItemsPage() {
  if (!$("cat-grid").childElementCount) loadCatalog();
  await playerOptions($("cg-target"), { everyone: true });
}

// ---- vehicles --------------------------------------------------------------
const CHASSIS = (id) => `Vehicle_${id}`;  // the whole vehicle's picture, not a part
async function openVehiclesPage() {
  if (!S.vehicles.length) {
    try { S.vehicles = (await api("/api/vehicles")).vehicles; } catch (e) { $("veh-error").textContent = e.message; return; }
    $("veh-grid").replaceChildren(...S.vehicles.map((v) => {
      const card = el("button", { type: "button", className: "item-card" }, itemImg(CHASSIS(v.id), "pic"),
        el("span", { className: "iname", textContent: v.name }), el("span", { className: "icat", textContent: `${v.variants.length} variant${v.variants.length === 1 ? "" : "s"}` }));
      card.addEventListener("click", () => pickVehicle(v, card));
      return card;
    }));
  }
  await playerOptions($("vg-player"), { onlineOnly: true });
}
function pickVehicle(v, card) {
  S.vehicle = v; S.variant = v.variants[v.variants.length - 1]?.id || null;
  document.querySelectorAll("#veh-grid .item-card").forEach((c) => c.removeAttribute("aria-pressed"));
  card.setAttribute("aria-pressed", "true");
  $("vg-empty").hidden = true; $("vg-body").hidden = false;
  setImg($("vg-img"), CHASSIS(v.id));
  $("vg-name").textContent = v.name; $("vg-result").textContent = "";
  $("vg-variants").replaceChildren(...v.variants.map((t) => {
    const b = el("button", { type: "button", textContent: t.name });
    b.setAttribute("aria-pressed", String(t.id === S.variant));
    b.addEventListener("click", () => {
      S.variant = t.id;
      $("vg-variants").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    });
    return b;
  }));
  if (window.matchMedia("(max-width: 900px)").matches) $("vg-body").scrollIntoView({ behavior: "smooth" });
}
$("vg-spawn").addEventListener("click", async () => {
  const player = $("vg-player").value;
  if (!S.vehicle || !S.variant || !player) { setResult("vg-result", false, "Pick a vehicle, a variant and an online player."); return; }
  const who = $("vg-player").selectedOptions[0].textContent.replace(" — online", "");
  if (!confirm(`Spawn a ${S.vehicle.name} (${S.vehicle.variants.find((t) => t.id === S.variant).name}) next to ${who}?`)) return;
  $("vg-result").className = "result"; $("vg-result").textContent = "Spawning…";
  try { const r = await api("/api/vehicles/spawn", { player, vehicle: S.vehicle.id, variant: S.variant }); setResult("vg-result", r.ok, r.output); }
  catch (e) { setResult("vg-result", false, e.message); }
});

Object.assign(window.WORLD, { items: openItemsPage, vehicles: openVehiclesPage });
