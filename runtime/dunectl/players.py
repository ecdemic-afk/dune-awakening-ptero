"""Player tools for the web panel: find a player by name, see them, act on them.

Reads are read-only SQL (the same queries the core's `players`, `player-state`,
`player-summary` and `inventory-list` operations run). Every write is an
existing core operation (admin-publish.sh), which already knows which changes
need the player offline and refuses otherwise — that refusal is shown as-is.
Players are addressed by their 16-hex FLS id in the API; names are for people.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time

FLS_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
TEMPLATE_RE = re.compile(r"^[A-Za-z0-9_]{1,120}$")

# One row per account (the core's `players` query, issue #121 there): the live
# player-state row wins over husks left by restores or interrupted transfers.
LIST_SQL = """
SELECT * FROM (
    SELECT DISTINCT ON (a.id)
           a."user" AS fls_id,
           COALESCE(convert_from(ps.encrypted_character_name, 'UTF8'), '') AS name,
           COALESCE(a.platform_id::text, '') AS steam_id,
           COALESCE(ps.life_state::text, '') AS life,
           COALESCE(ps.online_status::text, '') AS online,
           COALESCE(ps.last_avatar_activity::text, '') AS last_activity
    FROM dune.accounts a
    LEFT JOIN dune.encrypted_player_state ps ON ps.account_id = a.id
    ORDER BY a.id,
             (ps.player_controller_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM dune.actors ac WHERE ac.id = ps.player_controller_id)) DESC,
             ps.last_avatar_activity DESC NULLS LAST
) t
ORDER BY (t.online = 'Online') DESC, t.last_activity DESC
LIMIT 1000
"""

DEFAULT_KITS = [
    {"name": "Heal kit", "items": [{"id": "HealthPack_Channeled_4", "qty": 5}, {"id": "Bloodsack_T6", "qty": 3},
                                   {"id": "AntiRadiationPill", "qty": 5}]},
    {"name": "Hydrate", "items": [{"id": "HighCapacityLiterjon_06", "qty": 1},
                                  {"id": "WaterPack_Consumable", "qty": 10}, {"id": "Stilltent", "qty": 1}]},
    {"name": "Welcome kit", "items": [{"id": "Literjon", "qty": 1}, {"id": "WaterPack_Consumable", "qty": 5},
                                      {"id": "HealthPack_Channeled", "qty": 3}, {"id": "Stilltent", "qty": 1},
                                      {"id": "MelangeSpice", "qty": 50}]},
]


def parse_csv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text or "")))


def parse_kv(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        k, sep, v = line.partition("=")
        if sep:
            out[k.strip()] = v.strip()
    return out


def _int(value, lo: int, hi: int, what: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a whole number")
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a whole number") from None
    if not lo <= n <= hi:
        raise ValueError(f"{what} must be between {lo:,} and {hi:,}")
    return n


def action_argv(fls: str, action: str, body: dict, known_item=lambda t: True) -> list[str]:
    """Validate a panel action and turn it into the core operation's arguments."""
    if action == "give-item":
        template = str(body.get("template", ""))
        if not TEMPLATE_RE.match(template) or not known_item(template):
            raise ValueError(f"unknown item '{template}' — pick one from the search")
        return ["give-item", fls, template, str(_int(body.get("qty", 1), 1, 10000, "Quantity")),
                str(_int(body.get("quality", 0), 0, 5, "Quality"))]
    if action == "give-currency":
        return ["give-currency", fls, str(_int(body.get("amount"), -100_000_000, 100_000_000, "Solaris"))]
    if action == "award-char-xp":
        return ["award-char-xp", fls, str(_int(body.get("amount"), -100_000_000, 100_000_000, "XP"))]
    if action == "faction-rep":
        faction = str(body.get("faction", "")).lower()
        if faction not in ("atreides", "harkonnen"):
            raise ValueError("faction must be atreides or harkonnen")
        return ["faction-rep", fls, faction, str(_int(body.get("amount"), -1_000_000, 1_000_000, "Reputation"))]
    if action == "rename":
        name = str(body.get("name", "")).strip()
        if not 2 <= len(name) <= 32 or not name.isprintable():
            raise ValueError("the name must be 2-32 printable characters")
        return ["rename", fls, name]
    if action == "item-delete":
        return ["item-delete", str(_int(body.get("item_id"), 1, 2**62, "Item id"))]
    if action == "item-edit":
        return ["item-edit", str(_int(body.get("item_id"), 1, 2**62, "Item id")),
                f"stack={_int(body.get('stack'), 1, 100000, 'Stack')}",
                f"quality={_int(body.get('quality', 0), 0, 5, 'Quality')}"]
    if action == "tech-unlock":
        mode = str(body.get("mode", ""))
        if mode not in ("unlock-all", "lock-all"):
            raise ValueError("mode must be unlock-all or lock-all")
        return ["tech-unlock", fls, mode]
    if action == "teleport":  # TeleportTo snaps to the nearest safe spot; the map is the player's current one
        x, y, z = (_int(body.get(k), -3_000_000, 3_000_000, k.upper()) for k in ("x", "y", "z"))
        return ["tpsafe", fls, str(x), str(y), str(z)]
    if action in ("kick", "water", "grant-keystones"):
        return [action, fls]
    if action in ("clean", "reset"):  # wipe inventory / wipe XP and skills
        if body.get("confirm") is not True:
            raise ValueError("this cannot be undone — confirm it first")
        return [action, fls]
    raise ValueError(f"unknown action '{action}'")


class PlayerTools:
    def __init__(self, base: str, run_op, say):
        """run_op(argv, timeout) -> (rc, stdout, stderr) runs one core operation."""
        self.base, self.run_op, self.say = base, run_op, say
        self.data = os.path.join(base, "data", "admin")
        self.state = os.path.join(base, "server", "state")
        self._items: list[dict] | None = None
        self._kits_lock = threading.Lock()

    # ---- catalogue -----------------------------------------------------
    def items(self) -> list[dict]:
        if self._items is None:
            try:
                with open(os.path.join(self.data, "items.json"), encoding="utf-8") as fh:
                    self._items = [i for i in json.load(fh) if isinstance(i, dict) and i.get("id")]
            except (OSError, ValueError):
                self._items = []
        return self._items

    def item_names(self) -> dict:
        return {i["id"]: i.get("name") or i["id"] for i in self.items()}

    def search_items(self, q: str, limit: int = 40) -> list[dict]:
        q = q.strip().lower()
        if not q:
            return []
        words = q.split()
        hits = [i for i in self.items()
                if all(w in (i["id"] + " " + (i.get("name") or "")).lower() for w in words)]
        hits.sort(key=lambda i: (not (i.get("name") or "").lower().startswith(q), len(i.get("name") or "")))
        return [{"id": i["id"], "name": i.get("name") or i["id"], "category": i.get("category", "")}
                for i in hits[:limit]]

    def browse(self, q: str, cat: str, offset: int = 0, limit: int = 60) -> dict:
        """Catalogue page: filter by words and/or category, paged."""
        words = q.strip().lower().split()
        hits = [i for i in self.items() if (not cat or i.get("category") == cat)
                and all(w in (i["id"] + " " + (i.get("name") or "")).lower() for w in words)]
        if not words:
            hits.sort(key=lambda i: (i.get("name") or i["id"]).lower())
        counts: dict[str, int] = {}
        for i in self.items():
            counts[i.get("category") or "misc"] = counts.get(i.get("category") or "misc", 0) + 1
        page = hits[max(0, offset):max(0, offset) + limit]
        return {"total": len(hits), "offset": offset,
                "items": [{"id": i["id"], "name": i.get("name") or i["id"], "category": i.get("category", "")} for i in page],
                "categories": sorted(counts.items(), key=lambda kv: -kv[1])}

    def category_of(self, item_id: str) -> str:
        if not hasattr(self, "_cats"):
            self._cats = {i["id"]: i.get("category", "") for i in self.items()}
        return self._cats.get(item_id, "")

    # ---- reads ---------------------------------------------------------
    def _sql(self, sql: str) -> list[dict]:
        rc, out, err = self.run_op(["db-sql", sql], 20)
        if rc != 0 or not out.strip():
            raise RuntimeError((err or "the database did not answer — is the server running?").strip()[-300:])
        return parse_csv(out)

    def list(self) -> list[dict]:
        rows = self._sql(LIST_SQL)
        banned = self._banned_ids()
        return [{"fls_id": r["fls_id"], "name": r["name"] or r["fls_id"], "steam_id": r["steam_id"],
                 "online": r["online"] == "Online", "life": r["life"], "last_activity": r["last_activity"],
                 "banned": r["fls_id"].upper() in banned} for r in rows if r.get("fls_id")]

    def detail(self, fls: str) -> dict:
        rc, out, err = self.run_op(["player-state", fls], 20)
        state = parse_csv(out)
        if rc != 0 or not state:
            raise LookupError((err or "no such player").strip()[-300:])
        st = state[0]
        out_d = {"fls_id": fls, "online": st.get("online") == "Online", "life": st.get("life", ""),
                 "map": st.get("map", ""), "steam_id": st.get("platform_id", ""),
                 "last_activity": st.get("last_activity", ""), "summary": None, "inventory": [],
                 "inventory_error": "", "ban": self._ban(fls)}
        rc, out, err = self.run_op(["player-summary", fls], 20)
        if rc == 0:
            kv = parse_kv(out)
            out_d["summary"] = self._summary(kv)
        rc, out, err = self.run_op(["inventory-list", fls], 20)
        if rc == 0:
            names = self.item_names()
            out_d["inventory"] = [
                {"item_id": r["item_id"], "template": r["template_id"], "name": names.get(r["template_id"], r["template_id"]),
                 "qty": r["stack_size"], "quality": r["quality"], "durability": r["durability"],
                 "max_durability": r["max_durability"], "inventory": r["inv_type"], "slot": r["slot"]}
                for r in parse_csv(out)]
        else:
            out_d["inventory_error"] = (err or "").strip()[-200:]
        return out_d

    def _summary(self, kv: dict) -> dict:
        import admin_progression  # core: level/tier math
        def n(k):
            try:
                return int(kv.get(k) or 0)
            except ValueError:
                return 0
        houses = [{"name": admin_progression.faction_display_name(fid), "rep": n(f"rep_{fid}"),
                   "tier": admin_progression.faction_tier_name(fid, admin_progression.rep_to_tier(n(f"rep_{fid}")))}
                  for fid in (1, 2)]
        return {"solaris": n("solaris"), "xp": admin_progression.char_xp_summary(n("char_xp")),
                "alignment": admin_progression.faction_display_name(n("align")) if n("align") else "",
                "houses": houses}

    # ---- bans ----------------------------------------------------------
    def bans(self) -> list[dict]:
        import admin_ban
        return admin_ban.list_bans(self.state)

    def _banned_ids(self) -> set:
        try:
            return {b["fls_id"].upper() for b in self.bans()}
        except Exception:
            return set()

    def _ban(self, fls: str):
        import admin_ban
        try:
            return admin_ban.active_ban(self.state, fls)
        except Exception:
            return None

    # ---- actions -------------------------------------------------------
    def act(self, fls: str, action: str, body: dict, who: str) -> dict:
        if not FLS_RE.match(fls):
            raise ValueError("bad player id")
        fls = fls.upper()
        if action == "ban":
            import admin_ban
            hours = body.get("hours")
            payload = dict(body, fls_id=fls, duration_secs=None if not hours else _int(hours, 1, 24 * 3650, "Hours") * 3600)
            code, res, kick = admin_ban.handle_ban_request(self.base, payload, by=who)
            if code != 200:
                raise ValueError(res.get("error", "ban failed"))
            self.say(f"web panel ({who}): banned {body.get('name') or fls}"
                     + (f" for {hours}h" if hours else " permanently") + (f" — {body.get('reason')}" if body.get("reason") else ""))
            if kick:
                self.run_op(["kick", kick], 30)
            return {"ok": True, "output": "Banned" + (" and kicked." if kick else ".")}
        if action == "unban":
            import admin_ban
            code, res = admin_ban.handle_lift_request(self.base, fls)
            if code != 200:
                raise ValueError(res.get("error", "unban failed"))
            self.say(f"web panel ({who}): lifted the ban on {fls}")
            return {"ok": True, "output": "Ban lifted."}
        if action == "give-kit":
            kit = next((k for k in self.kits() if k["name"] == body.get("kit")), None)
            if not kit:
                raise ValueError("no such kit")
            lines = []
            for item in kit["items"]:
                argv = action_argv(fls, "give-item", {"template": item["id"], "qty": item["qty"]}, self._known)
                rc, out, err = self.run_op(argv, 60)
                lines.append(f"{'✔' if rc == 0 else '✘'} {item['qty']} × {self.item_names().get(item['id'], item['id'])}"
                             + ("" if rc == 0 else f" — {(err or out).strip()[-160:]}"))
            self.say(f"web panel ({who}): gave kit '{kit['name']}' to {fls}")
            return {"ok": all(l.startswith("✔") for l in lines), "output": "\n".join(lines)}
        argv = action_argv(fls, action, body, self._known)
        rc, out, err = self.run_op(argv, 120)
        self.say(f"web panel ({who}): {action} {fls}" + ("" if rc == 0 else " — failed"),
                 "INFO" if rc == 0 else "WARN")
        text = "\n".join(l.replace("[admin-publish] ", "") for l in (out + "\n" + err).splitlines()
                         if l.strip() and not l.startswith("publish="))
        return {"ok": rc == 0, "output": text.strip()[-1500:] or ("Done." if rc == 0 else "Failed.")}

    def _known(self, template: str) -> bool:
        return not self.items() or template in self.item_names()

    # ---- gifts ---------------------------------------------------------
    def gift(self, body: dict, who: str) -> dict:
        """A kit or one item to one player or to everyone online."""
        players = self.list()
        names = {p["fls_id"].upper(): p["name"] for p in players}
        target = str(body.get("target", ""))
        if target == "online":
            ids = [p["fls_id"].upper() for p in players if p["online"]]
        elif FLS_RE.match(target):
            ids = [target.upper()]
        else:
            raise ValueError("pick a player, or everyone online")
        if not ids:
            return {"ok": False, "output": "Nobody is online."}
        what = ("give-kit", {"kit": body.get("kit")}) if body.get("kit") else \
            ("give-item", {"template": body.get("template"), "qty": body.get("qty", 1)})
        if what[0] == "give-item":
            action_argv(ids[0], what[0], what[1], self._known)  # a bad item fails once, not once per player
        elif not any(k["name"] == body.get("kit") for k in self.kits()):
            raise ValueError("no such kit")
        lines = []
        for fls in ids:
            r = self.act(fls, what[0], what[1], who)
            last = r["output"].strip().splitlines()[-1] if r["output"].strip() else ""
            lines.append(f"{'✔' if r['ok'] else '✘'} {names.get(fls, fls)}" + ("" if r["ok"] else f" — {last}"))
        return {"ok": all(l.startswith("✔") for l in lines), "output": "\n".join(lines)}

    # ---- welcome gift (core: admin_welcome — once per player per version) ----
    def welcome(self) -> dict:
        import admin_welcome
        try:
            cfg = admin_welcome.load_config(self.base)
        except (OSError, ValueError):
            cfg = {}
        return {"enabled": bool(cfg.get("enabled")), "kit": cfg.get("panel_kit", ""),
                "announce": bool(cfg.get("announce")),
                "announce_text": cfg.get("announce_text") or "{name} just landed on Arrakis — welcome!",
                "since": cfg.get("panel_since", "")}

    def save_welcome(self, body: dict, who: str) -> dict:
        import admin_welcome
        try:
            cfg = admin_welcome.load_config(self.base)
        except (OSError, ValueError):
            cfg = {}
        enabled = bool(body.get("enabled"))
        kit = next((k for k in self.kits() if k["name"] == body.get("kit")), None)
        if enabled and not kit:
            raise ValueError("pick the kit new players get")
        text = str(body.get("announce_text") or "").strip()[:200]
        new_round = kit and (kit["name"] != cfg.get("panel_kit") or body.get("regrant"))
        if new_round or not cfg.get("active_version"):
            # A new version is a new round: everyone (not just new players) gets it once more.
            cfg["active_version"] = time.strftime("v%Y%m%d-%H%M%S")
            cfg["panel_since"] = time.strftime("%Y-%m-%d %H:%M")
        items = [{"template": i["id"], "qty": int(i["qty"]), "quality": 0} for i in (kit or {"items": []})["items"]]
        if enabled:
            admin_welcome.validate_items(items)
        cfg.update({"enabled": enabled, "panel_kit": kit["name"] if kit else "", "scan_interval_secs": 60,
                    "announce": bool(body.get("announce")), "announce_title": "Welcome!",
                    "announce_text": text or "{name} just landed on Arrakis — welcome!",
                    "packages": [{"version": cfg["active_version"], "items": items}]})
        self._write_welcome(cfg)
        self.say(f"web panel ({who}): welcome gift {'on — ' + kit['name'] if enabled else 'off'}"
                 + (" (new round: everyone gets it once more)" if new_round and body.get("regrant") else ""))
        return self.welcome()

    def _write_welcome(self, cfg: dict) -> None:
        import admin_welcome
        p = admin_welcome.config_path(self.base)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=1)
        os.replace(p + ".tmp", p)

    def _sync_welcome(self, kits: list[dict]) -> None:
        """Editing the welcome kit updates the gift without re-giving it to everyone."""
        import admin_welcome
        try:
            cfg = admin_welcome.load_config(self.base)
        except (OSError, ValueError):
            return
        kit = next((k for k in kits if k["name"] == cfg.get("panel_kit")), None)
        if kit and cfg.get("packages"):
            cfg["packages"][0]["items"] = [{"template": i["id"], "qty": int(i["qty"]), "quality": 0} for i in kit["items"]]
            self._write_welcome(cfg)

    # ---- kits ----------------------------------------------------------
    def _kits_path(self) -> str:
        return os.path.join(self.state, "panel-kits.json")

    def kits(self) -> list[dict]:
        try:
            with open(self._kits_path(), encoding="utf-8") as fh:
                kits = json.load(fh)
            if isinstance(kits, list):
                return kits
        except (OSError, ValueError):
            pass
        return DEFAULT_KITS

    def save_kits(self, kits) -> list[dict]:
        if not isinstance(kits, list) or len(kits) > 100:
            raise ValueError("kits must be a list (at most 100)")
        clean = []
        for k in kits:
            name = str((k or {}).get("name", "")).strip()[:40]
            items = (k or {}).get("items") or []
            if not name or not isinstance(items, list) or not 1 <= len(items) <= 50:
                raise ValueError(f"kit '{name or '?'}' needs a name and 1-50 items")
            lines = []
            for it in items:
                tid = str((it or {}).get("id", ""))
                if not TEMPLATE_RE.match(tid) or not self._known(tid):
                    raise ValueError(f"kit '{name}': unknown item '{tid}'")
                lines.append({"id": tid, "qty": _int(it.get("qty", 1), 1, 10000, "Quantity")})
            clean.append({"name": name, "items": lines})
        with self._kits_lock:
            os.makedirs(self.state, exist_ok=True)
            tmp = self._kits_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(clean, fh, indent=1)
            os.replace(tmp, self._kits_path())
        self._sync_welcome(clean)
        return clean
