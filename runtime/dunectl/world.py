"""World tools for the web panel: live map, bases, guilds, and the operations catalogue.

Same rule as players.py: reads are the core's read-only operations (CSV), every
write is an existing core operation that keeps its own safety checks.
"""
from __future__ import annotations

import json
import os
import re

from .players import FLS_RE, parse_csv

MAPS = {  # key -> (label, image, minX, maxX, minY, maxY, note). Bounds from the old panel (dune-admin, MIT).
    "HaggaBasin": ("Hagga Basin", "hagga-basin.webp", -457200, 355600, -457200, 355600, ""),
    "DeepDesert": ("Deep Desert", "deepdesert.webp", -1270000, 1168400, -1270000, 1168400, ""),
    "Arrakeen": ("Arrakeen", "arrakeen.webp", -32000, 17000, -10000, 9500,
                 "this map image is incomplete, so positions are approximate"),
    "HarkoVillage": ("Harko Village", "harko.webp", -5000, 14500, -5500, 32000,
                     "this map image is incomplete, so positions are approximate"),
}
VEHICLE_NAMES = {"Sandbike": "Sandbike", "Buggy": "Buggy", "Tank": "Assault Tank", "Sandcrawler": "Sandcrawler",
                 "OrnithopterLight": "Scout Ornithopter", "OrnithopterMedium": "Assault Ornithopter",
                 "OrnithopterTransport": "Carrier Ornithopter", "TreadWheel": "Treadwheel",
                 "ContainerVehicle": "Carrier cargo container"}


def variant_name(template: str) -> str:
    """'T6_CombatFire' -> 'Mk6 Combat Fire'."""
    m = re.match(r"^T(\d)_?(.*)$", template)
    if not m:
        return template.replace("_", " ")
    rest = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", m.group(2).replace("_", " ")).strip()
    return f"Mk{m.group(1)}" + (f" {rest}" if rest else "")


ID_RE = re.compile(r"^\d{1,19}$")
GUILD_NAME_RE = re.compile(r"^[^\x00-\x1f]{2,32}$")

# "#   op <arg> [opt=1]   -- description" lines in admin-publish.sh's header.
OP_LINE = re.compile(r"^#\s{2,4}([a-z][a-z0-9-]*)((?:\s+(?:<[^>]+>|\[[^\]]+\]))*)\s*(?:--\s+(.+))?$")
ARG = re.compile(r"<([^>]+)>|\[([^\]=]+)(?:=([^\]]*))?\]")
# Operations the panel already covers with its own pages, or that only make
# sense from a shell; hidden from the Commands list to keep it readable.
HIDDEN_OPS = {"raw", "exec", "cheat", "resolve", "db-sql", "db-restore", "chat-drain",
              "chat-queue-init", "chat-queue-drop"}


def parse_catalogue(script_text: str) -> list[dict]:
    ops, last = [], None
    for line in script_text.splitlines()[:400]:
        if not line.startswith("#"):
            if ops:
                break
            continue
        m = OP_LINE.match(line)
        if m:
            name, argtext, desc = m.groups()
            args = [{"name": req or opt, "optional": bool(opt), "default": dflt or ""}
                    for req, opt, dflt in ARG.findall(argtext)]
            last = {"op": name, "args": args, "desc": (desc or "").strip()}
            if name not in HIDDEN_OPS and not any(o["op"] == name for o in ops):
                ops.append(last)
            continue
        cont = line[1:].strip()
        if last is not None and line.startswith("#" + " " * 20) and cont:
            last["desc"] += " " + cont.lstrip("- ").strip()
        else:
            last = None
    for o in ops:
        o["danger"] = bool(re.search(r"DESTRUCTIVE|IRREVERSIBLE|delete|wipe", o["desc"], re.I))
    return ops


class WorldTools:
    def __init__(self, base: str, scripts: str, run_op, say):
        self.base, self.scripts, self.run_op, self.say = base, scripts, run_op, say
        self.queue = os.path.join(base, "server", "state", "refill-queue")
        self._ops: list[dict] | None = None

    def _csv(self, argv: list[str], timeout: int = 30) -> list[dict]:
        rc, out, err = self.run_op(argv, timeout)
        if rc != 0:
            raise RuntimeError((err or out or f"{argv[0]} failed").replace("[admin-publish] ", "").strip()[-300:])
        return parse_csv(out)

    def _write(self, argv: list[str], who: str, what: str, timeout: int = 120) -> dict:
        rc, out, err = self.run_op(argv, timeout)
        self.say(f"web panel ({who}): {what}" + ("" if rc == 0 else " — failed"), "INFO" if rc == 0 else "WARN")
        text = "\n".join(l.replace("[admin-publish] ", "") for l in (out + "\n" + err).splitlines()
                         if l.strip() and not l.startswith("publish="))
        return {"ok": rc == 0, "output": text.strip()[-1500:] or ("Done." if rc == 0 else "Failed.")}

    # ---- map -----------------------------------------------------------
    def maps(self) -> list[dict]:
        return [{"key": k, "label": v[0], "image": f"maps/{v[1]}", "minX": v[2], "maxX": v[3],
                 "minY": v[4], "maxY": v[5], "note": v[6]} for k, v in MAPS.items()]

    def markers(self, key: str) -> list[dict]:
        if key not in MAPS:
            raise ValueError("unknown map")
        import admin_map  # core: tolerant CSV -> marker parsing
        rc, out, err = self.run_op(["map-markers", key], 20)
        if rc != 0:
            raise RuntimeError((err or "map query failed").strip()[-300:])
        return admin_map.parse_markers(out)

    def resources(self, key: str) -> dict:
        """Deep Desert points of interest for the week's layout (12 fixed layouts, picked by the
        Coriolis seed; community-collected data via DST, Apache-2.0), plus on Hagga Basin and the
        Deep Desert every spot a small ship can crash (crash_sites)."""
        sites = self.crash_sites(key)
        if key != "DeepDesert":
            return {"available": False, "crash_sites": sites,
                    "reason": "Resource locations are only known for the Deep Desert (it rotates through 12 fixed layouts)."}
        import admin_wickmaps
        rc, out, _ = self.run_op(["coriolis-seed"], 20)
        view = admin_wickmaps.deepdesert_view(self.base, parse_csv(out) if rc == 0 else [])
        if view["seed"] is None:
            view["reason"] = "The Deep Desert has not reported this week's layout yet (it needs to be running)."
        return {"available": view["layout_available"], "crash_sites": sites, **view}

    def crash_sites(self, key: str) -> list:
        """[[x, y, z], ...] where small ships can crash on this map. The crashes themselves are never
        saved or logged with a position (SkipSaving), so these known spots are all a server can show."""
        try:
            with open(os.path.join(self.base, "data", "admin", "crash-sites.json"), encoding="utf-8") as fh:
                sites = json.load(fh).get("maps", {}).get(key, [])
        except (OSError, ValueError, AttributeError):
            return []
        return [p for p in sites if isinstance(p, list) and len(p) >= 2]

    # ---- positions, saved locations, vehicles ---------------------------
    def position(self, fls: str) -> dict:
        """Where a player is: their map (player-state) and last saved coordinates (map-markers)."""
        fls = fls.upper()
        st = self._csv(["player-state", fls], 20)
        if not st:
            raise LookupError("no such player")
        st = st[0]
        game_map = st.get("map", "")
        if game_map not in MAPS:
            raise ValueError(f"{fls} is on '{game_map or 'no map'}', which has no map page")
        mk = next((m for m in self.markers(game_map) if str(m.get("fls", "")).upper() == fls), None)
        if mk is None:
            raise ValueError("their position isn't saved yet — try again in a few seconds")
        return {"map": game_map, "x": mk["x"], "y": mk["y"], "z": mk["z"], "online": st.get("online") == "Online"}

    def locations(self) -> list[dict]:
        import admin_locations
        return admin_locations.load_locations(self.base)

    def save_location(self, body: dict, who: str) -> list[dict]:
        import admin_locations
        raw = dict(body)
        if body.get("from_player"):  # exactly where that player stands
            pos = self.position(str(body["from_player"]))
            raw.update(map=pos["map"], x=pos["x"], y=pos["y"], z=pos["z"])
        locs, err = admin_locations.upsert_location(self.base, raw)
        if err:
            raise ValueError(err)
        self.say(f"web panel ({who}): saved location '{raw.get('name')}' on {raw.get('map')}")
        return locs

    def delete_location(self, name: str, who: str) -> list[dict]:
        import admin_locations
        self.say(f"web panel ({who}): removed location '{name}'")
        return admin_locations.remove_location(self.base, name)

    def teleport_to(self, fls: str, name: str, who: str) -> dict:
        loc = next((l for l in self.locations() if l["name"].lower() == str(name).lower()), None)
        if loc is None:
            raise ValueError("no such location")
        pos = self.position(fls)
        if pos["map"] != loc["map"]:
            # TeleportTo carries no map: these coordinates would be used inside the map they're on.
            raise ValueError(f"they are on {MAPS[pos['map']][0]}, but '{loc['name']}' is on {MAPS[loc['map']][0]}")
        return self._write(["tpsafe", fls.upper(), str(round(loc["x"])), str(round(loc["y"])), str(round(loc["z"]))],
                           who, f"teleported {fls} to '{loc['name']}'")

    def vehicles(self) -> list[dict]:
        try:
            with open(os.path.join(self.base, "data", "admin", "vehicles.json"), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        return [{"id": v["id"], "name": VEHICLE_NAMES.get(v["id"], v["id"]),
                 "variants": [{"id": t, "name": variant_name(t)} for t in v.get("templates", [])]}
                for v in data if isinstance(v, dict) and v.get("id")]

    def spawn_vehicle(self, fls: str, vehicle: str, variant: str, who: str) -> dict:
        if not FLS_RE.match(fls):
            raise ValueError("pick a player")
        v = next((x for x in self.vehicles() if x["id"] == vehicle), None)
        if v is None or variant not in [t["id"] for t in v["variants"]]:
            raise ValueError("pick a vehicle and a variant from the list")
        pos = self.position(fls)
        if not pos["online"]:
            raise ValueError("the player must be online — the vehicle appears next to them")
        # A few metres beside them and a little up, so it doesn't spawn inside the player.
        x, y, z = round(pos["x"]) + 600, round(pos["y"]), round(pos["z"]) + 150
        res = self._write(["vehicle", fls.upper(), vehicle, str(x), str(y), str(z), variant], who,
                          f"spawned {v['name']} ({variant_name(variant)}) for {fls}")
        if res["ok"]:
            res["output"] = f"{v['name']} ({variant_name(variant)}) spawned next to them on {MAPS[pos['map']][0]}."
        return res

    # ---- bases ---------------------------------------------------------
    def bases(self) -> list[dict]:
        return self._csv(["bases"])

    def base_detail(self, bid: str) -> dict:
        if not ID_RE.match(bid):
            raise ValueError("bad base id")
        out = {"base_id": bid, "water": [], "fuel": [], "errors": [], "queued": self.queued(bid)}
        for kind in ("water", "fuel"):
            try:
                out[kind] = self._csv([f"base-{kind}", bid])
            except RuntimeError as exc:
                out["errors"].append(f"{kind}: {exc}")
        return out

    def queued(self, bid: str | None = None) -> list[str]:
        try:
            with open(self.queue, encoding="utf-8") as fh:
                rows = [l.split() for l in fh if len(l.split()) >= 2]
        except OSError:
            return []
        return [k for k, b, *_ in rows if bid is None or b == bid]

    def refill(self, bid: str, kind: str, who: str) -> dict:
        if not ID_RE.match(bid) or kind not in ("water", "fuel"):
            raise ValueError("bad refill request")
        res = self._write([f"base-{kind}-refill", bid], who, f"{kind} refill for base #{bid}")
        if res["ok"]:
            return res
        if "live server" in res["output"]:  # the map is running: queue it for the next start
            if kind not in self.queued(bid):
                os.makedirs(os.path.dirname(self.queue), exist_ok=True)
                with open(self.queue, "a", encoding="utf-8") as fh:
                    fh.write(f"{kind} {bid}\n")
            self.say(f"web panel ({who}): {kind} refill for base #{bid} queued for the next restart")
            return {"ok": True, "queued": True,
                    "output": f"That map is running, so the {kind} refill is queued: it is applied at the next "
                              "restart, before the map loads."}
        return res

    # ---- guilds --------------------------------------------------------
    def guilds(self) -> list[dict]:
        return self._csv(["guilds"])

    def guild(self, gid: str) -> dict:
        if not ID_RE.match(gid):
            raise ValueError("bad guild id")
        return {"guild_id": gid, "members": self._csv(["guild-members", gid]),
                "invites": self._csv(["guild-invites", gid])}

    def guild_act(self, gid: str, action: str, body: dict, who: str) -> dict:
        if not ID_RE.match(gid):
            raise ValueError("bad guild id")
        if action == "describe":
            text = str(body.get("description", "")).strip()[:500]
            return self._write(["guild-describe", gid, text], who, f"guild #{gid} description")
        if action == "rename":
            name = str(body.get("name", "")).strip()
            if not GUILD_NAME_RE.match(name):
                raise ValueError("the name must be 2-32 characters")
            return self._write(["guild-rename", gid, name], who, f"guild #{gid} renamed to {name}")
        if action == "faction":
            faction = str(body.get("faction", "")).lower()
            if faction not in ("atreides", "harkonnen", "none"):
                raise ValueError("faction must be atreides, harkonnen or none")
            return self._write(["guild-set-faction", gid, faction], who, f"guild #{gid} pledged to {faction}")
        if action in ("set-role", "kick"):
            member = str(body.get("player_id", ""))
            if not ID_RE.match(member) and not FLS_RE.match(member):
                raise ValueError("bad member id")
            if action == "kick":
                return self._write(["guild-kick", gid, member], who, f"kicked {member} from guild #{gid}")
            role = str(body.get("role", ""))
            if role not in ("50", "100"):
                raise ValueError("role must be 50 (member) or 100 (leader)")
            return self._write(["guild-set-role", gid, member, role], who, f"guild #{gid} role change")
        if action == "disband":
            if body.get("confirm") is not True:
                raise ValueError("confirm first")
            return self._write(["guild-disband", gid], who, f"disbanded guild #{gid}")
        raise ValueError(f"unknown action '{action}'")

    def guild_create(self, body: dict, who: str) -> dict:
        leader, name = str(body.get("leader", "")), str(body.get("name", "")).strip()
        if not FLS_RE.match(leader):
            raise ValueError("pick the leader from the list")
        if not GUILD_NAME_RE.match(name):
            raise ValueError("the name must be 2-32 characters")
        argv = ["guild-create", leader.upper(), name]
        if body.get("description"):
            argv.append(str(body["description"])[:500])
        return self._write(argv, who, f"created guild {name}")

    # ---- commands ------------------------------------------------------
    def ops(self) -> list[dict]:
        if self._ops is None:
            try:
                with open(os.path.join(self.scripts, "admin-publish.sh"), encoding="utf-8") as fh:
                    self._ops = parse_catalogue(fh.read())
            except OSError:
                self._ops = []
        return self._ops

    def run(self, op: str, args: list, who: str) -> dict:
        spec = next((o for o in self.ops() if o["op"] == op), None)
        if spec is None:
            raise ValueError(f"unknown operation '{op}'")
        args = [str(a) for a in (args or [])]
        while args and args[-1] == "":
            args.pop()
        required = sum(1 for a in spec["args"] if not a["optional"])
        if len(args) < required or len(args) > len(spec["args"]):
            raise ValueError(f"{op} needs {required}-{len(spec['args'])} values")
        if any(a == "" or "\n" in a or len(a) > 500 for a in args):
            raise ValueError("fill the fields in order; values must be one line")
        return self._write([op, *args], who, f"{op} {' '.join(args)}"[:200], timeout=300)
