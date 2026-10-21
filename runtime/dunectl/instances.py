"""Instances page: every map instance with its players and memory, and start / stop.

Two kinds of instance, each controlled the way the core does it:
  - maps the Kubernetes stand-in runs (hubs, dungeons, story maps, the always-running
    maps): stopped / started by setting their ServerSetScale replicas to 0 / 1;
  - map copies ("dimensions": Deep Desert copies, extra sietches): admin-publish
    dimension-down / dimension-up (and the parked list, so a stopped copy stays stopped).
Stopping an instance with players on it needs a second, confirmed request.
"""
from __future__ import annotations

import os

from .discord import instance_name
from .players import parse_csv

GAME_BINARY = "DuneSandboxServer-Linux-Shipping"
HUBS = ("Overmap", "DeepDesert_1", "SH_Arrakeen", "SH_HarkoVillage")  # always offered, even when stopped
MAIN = ("Survival_1", 0)  # the main sietch: players live here; stopping it is a server restart's job

FARM_SQL = ("SELECT wp.partition_id AS partition, wp.map, COALESCE(wp.dimension_index, 0) AS dim, "
            "COALESCE(wp.label, '') AS label, COALESCE(fs.connected_players, 0) AS players, fs.ready, fs.alive "
            "FROM dune.world_partition wp LEFT JOIN dune.farm_state fs ON fs.server_id = wp.server_id "
            "ORDER BY wp.map, wp.dimension_index")


def game_processes(proc: str = "/proc") -> dict[int, dict]:
    """partition id -> {pid, rss (bytes), started (epoch)} for every running game server."""
    out: dict[int, dict] = {}
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        try:
            with open(f"{proc}/{pid}/cmdline", "rb") as fh:
                args = fh.read().split(b"\0")
            if not args or not args[0].endswith(GAME_BINARY.encode()):
                continue
            part = next((int(a.split(b"=", 1)[1]) for a in args if a.startswith(b"-PartitionIndex=")), None)
            if part is None:
                continue
            rss = 0
            with open(f"{proc}/{pid}/status") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1]) * 1024
            started = os.stat(f"{proc}/{pid}").st_mtime
        except (OSError, ValueError, IndexError):
            continue
        cur = out.get(part)
        out[part] = {"pid": int(pid), "rss": rss + (cur["rss"] if cur else 0), "started": started}
    return out


def build_rows(mock: dict | None, farm: list[dict], procs: dict[int, dict], parked: set[int]) -> list[dict]:
    """Pure: one row per instance the operator can see or act on."""
    scale = {m.get("map"): m for m in (mock or {}).get("maps", [])}
    known = {m.get("map"): m for m in (mock or {}).get("knownMaps", [])}
    rows = []
    for r in farm:
        try:
            part, dim, players = int(r["partition"]), int(r.get("dim") or 0), int(r.get("players") or 0)
        except (KeyError, ValueError):
            continue
        game_map = r.get("map", "")
        alive = str(r.get("alive", "")).lower() in ("t", "true")
        ready = str(r.get("ready", "")).lower() in ("t", "true")
        proc = procs.get(part)
        running = bool(proc) or alive
        if dim > 0:
            kind = "copy"
        elif game_map in scale or game_map in known or game_map in HUBS:
            kind = "map"
        else:
            continue  # a partition no one can start from here
        if not running and kind == "map" and game_map not in HUBS and game_map not in scale:
            continue  # dungeons and story maps only show while they run
        sc = scale.get(game_map, {})
        state = ("starting" if running and not ready else "running") if running else \
            ("failing" if sc.get("status") == "failing" else "stopped")
        rows.append({
            "partition": part, "map": game_map, "dim": dim, "kind": kind,
            "name": instance_name(game_map, dim, r.get("label", "")),
            "state": state, "players": players if alive else 0,
            "ram": (proc or {}).get("rss", 0), "since": (proc or {}).get("started"),
            "parked": part in parked, "locked": (game_map, dim) == MAIN,
        })
    rows.sort(key=lambda x: (x["locked"] is False, x["state"] == "stopped", x["map"], x["dim"]))
    return rows


class InstanceControl:
    def __init__(self, base: str, scripts: str, run_op, say):
        self.base, self.scripts, self.run_op, self.say = base, scripts, run_op, say

    def _mock(self):
        import admin_instances  # core: CA-pinned client for the Kubernetes stand-in
        return admin_instances.fetch_mock_status()

    def _farm(self) -> list[dict]:
        rc, out, err = self.run_op(["db-sql", FARM_SQL], 20)
        if rc != 0 or not out.strip():
            raise RuntimeError((err or "the database did not answer — is the server running?").strip()[-300:])
        return parse_csv(out)

    def _parked(self) -> set[int]:
        import admin_park
        return admin_park.parked_ids(self.base)

    def state(self) -> dict:
        mock = self._mock()
        rows = build_rows(mock, self._farm(), game_processes(), self._parked())
        return {"instances": rows, "game_ram": sum(r["ram"] for r in rows),
                "stand_in": mock is not None}

    def act(self, partition: int, action: str, force: bool, who: str) -> dict:
        if action not in ("start", "stop"):
            raise ValueError("action must be start or stop")
        rows = build_rows(self._mock(), self._farm(), game_processes(), self._parked())
        row = next((r for r in rows if r["partition"] == partition), None)
        if row is None:
            raise ValueError("no such instance")
        if row["locked"]:
            raise ValueError("Hagga Basin's main sietch is where players live — it stops with the server. "
                             "Use Restart on the Overview page instead.")
        if action == "stop" and row["players"] and not force:
            return {"ok": False, "confirm": True,
                    "output": f"{row['players']} player(s) are on {row['name']}. They will be disconnected."}
        if row["kind"] == "copy":
            return self._copy(row, action, who)
        return self._scale(row, 0 if action == "stop" else 1, who)

    def _copy(self, row: dict, action: str, who: str) -> dict:
        import admin_park
        pid = str(row["partition"])
        if action == "stop":
            admin_park.park(self.base, row["partition"])  # stays stopped, also across restarts, until started here
            rc, out, err = self.run_op(["dimension-down", pid], 90)
        else:
            admin_park.unpark(self.base, row["partition"])
            rc, out, err = self.run_op(["dimension-up", pid], 60)
        self.say(f"web panel ({who}): {action} {row['name']}" + ("" if rc == 0 else " — failed"),
                 "INFO" if rc == 0 else "WARN")
        if rc != 0:
            return {"ok": False, "output": (err or out).replace("[admin-publish] ", "").strip()[-400:]}
        note = (" It stays stopped after restarts until you start it here." if action == "stop" else
                " It takes a few minutes to load. If it is above the copy count in Settings, the next "
                "restart stops it again — raise the count to keep it.")
        return {"ok": True, "output": f"{row['name']} {'stopped' if action == 'stop' else 'starting'}.{note}"}

    def _scale(self, row: dict, replicas: int, who: str) -> dict:
        import admin_instances
        mock = self._mock()
        if not mock:
            return {"ok": False, "output": "The Kubernetes stand-in (mock-k8s) is not answering — is the server running?"}
        key = admin_instances.resolve_scale_key(mock, row["map"])
        if key is None:  # never started: ask the stand-in to create its record first (as the old panel did)
            canonical = admin_instances.known_map_name(mock, row["map"])
            if canonical:
                admin_instances.mock_k8s_request(
                    "GET", f"/apis/igw.funcom.com/v1/namespaces/default/serversetscales/{canonical}")
                key = admin_instances.resolve_scale_key(admin_instances.fetch_mock_status(), row["map"])
        if key is None:
            return {"ok": False, "output": f"{row['name']} can't be started from here (not in this world's map list)."}
        ns, name, _ = key
        code, _resp = admin_instances.mock_k8s_request(
            "PATCH", f"/apis/igw.funcom.com/v1/namespaces/{ns}/serversetscales/{name}", {"spec": {"replicas": replicas}})
        ok = code is not None and code < 300
        self.say(f"web panel ({who}): {'stop' if replicas == 0 else 'start'} {row['name']}" + ("" if ok else " — failed"),
                 "INFO" if ok else "WARN")
        if not ok:
            return {"ok": False, "output": f"The stand-in refused (HTTP {code})."}
        return {"ok": True, "output": f"{row['name']} {'stopping' if replicas == 0 else 'starting — a few minutes to load'}."
                + (" It starts again when a player travels there." if replicas == 0 else "")}
