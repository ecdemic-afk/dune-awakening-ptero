"""Discord webhook alerts.

Sent from a background thread so a slow or unreachable Discord can never hold
up the server. Identical alerts within 10 minutes are sent once (a crash loop
must not flood the channel), and Discord's 429 rate limit is honoured.
The webhook URL is a secret: it is never printed.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request

COLORS = {"info": 0x3B82F6, "good": 0x22C55E, "warn": 0xF59E0B, "bad": 0xEF4444}
DEDUPE_SECS = 600
# Event kind -> the [discord] switch that enables it.
SWITCH = {"start": "notify_start", "stop": "notify_start", "crash": "notify_crash",
          "update": "notify_updates", "backup": "notify_backups", "health": "notify_health",
          "player": "notify_players"}


def payload(server: str, title: str, text: str, tone: str) -> dict:
    return {
        "username": server[:80] or "Dune Server",
        "embeds": [{
            "title": title[:256],
            "description": text[:4000],
            "color": COLORS.get(tone, COLORS["info"]),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }],
    }


MAP_NAMES = {"Survival_1": "Hagga Basin", "DeepDesert_1": "Deep Desert", "Overmap": "Overland",
             "SH_Arrakeen": "Arrakeen", "SH_HarkoVillage": "Harko Village", "SH_FallenLight": "Fallen Light"}
STATES = {  # state -> (label, colour)
    "running": ("🟢 Online", COLORS["good"]), "starting": ("🟡 Starting", COLORS["warn"]),
    "restarting": ("🟠 Restarting", COLORS["warn"]), "restoring": ("🟠 Restoring a backup", COLORS["warn"]),
    "held": ("⚠️ Waiting for an admin", COLORS["bad"]), "stopping": ("🔴 Offline", COLORS["bad"]),
    "offline": ("🔴 Offline", COLORS["bad"]),
}


def instance_name(game_map: str, dim: int, label: str) -> str:
    """'DeepDesert_1', 1, 'Deep Desert D1' -> 'Deep Desert D1'; dungeons get a readable name."""
    if dim > 0 and label:
        return label
    name = MAP_NAMES.get(game_map)
    if not name:
        name = game_map.replace("CB_Dungeon_", "").replace("SH_", "").replace("_", " ").strip()
    return name if dim <= 0 else f"{name} #{dim + 1}"


def _ago(secs: int) -> str:
    h, m = divmod(max(0, secs) // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def status_embed(server: str, state: str, instances: list[dict], joinable: bool = True,
                 pending: dict | None = None, uptime: int = 0) -> dict:
    """The live status message: state, players in total and on each map instance."""
    label, color = STATES.get(state, STATES["offline"])
    total = sum(i.get("players", 0) for i in instances)
    lines = []
    if state == "running":
        lines.append(f"**{total} player{'' if total == 1 else 's'} online**"
                     + ("" if joinable else " · maps still loading"))
    if pending:
        lines.append(f"⏳ {pending['kind'].capitalize()} in {_ago(pending['in'])}"
                     + (f" — {pending['reason']}" if pending.get("reason") else ""))
    fields = []
    if state == "running":
        for i in instances[:24]:
            fields.append({"name": i["name"], "inline": True,
                           "value": f"{i['players']} player{'' if i['players'] == 1 else 's'}"
                                    if i.get("ready", True) else "starting…"})
    return {
        "title": f"{server} — {label}"[:256],
        "description": "\n".join(lines) or ("The server is down." if state in ("offline", "stopping") else "…"),
        "color": color, "fields": fields,
        "footer": {"text": (f"Up {_ago(uptime)} · " if state == "running" and uptime else "") + "Updated every minute"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


class LiveStatus:
    """One message in the channel, edited in place (webhooks can edit what they posted).
    Its id is kept in a state file so restarts keep editing the same message; if someone
    deletes it, a new one is posted."""

    def __init__(self, url: str, state_path: str, server: str, say, opener=urllib.request.urlopen):
        self.url, self.path, self.server, self.say, self.opener = url.strip().rstrip("/"), state_path, server, say, opener
        self.lock = threading.Lock()

    def _id(self) -> str:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh).get("message_id", "")
        except (OSError, ValueError):
            return ""

    def _save(self, mid: str) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"message_id": mid}, fh)
        os.replace(self.path + ".tmp", self.path)

    def _request(self, method: str, url: str, body: dict, timeout: int):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method=method,
                                     headers={"Content-Type": "application/json", "User-Agent": "dune-awakening-ptero"})
        with self.opener(req, timeout=timeout) as res:
            raw = res.read()
        return json.loads(raw) if raw else {}

    def update(self, embed: dict, timeout: int = 10) -> bool:
        if not self.url:
            return False
        with self.lock:
            mid = self._id()
            try:
                if mid:
                    try:
                        self._request("PATCH", f"{self.url}/messages/{mid}", {"embeds": [embed]}, timeout)
                        return True
                    except urllib.error.HTTPError as exc:
                        if exc.code != 404:  # 404: the message was deleted — post a new one
                            raise
                msg = self._request("POST", f"{self.url}?wait=true",
                                    {"username": self.server[:80] or "Dune Server", "embeds": [embed]}, timeout)
                if msg.get("id"):
                    self._save(str(msg["id"]))
                return True
            except urllib.error.HTTPError as exc:
                if exc.code != 429:  # rate limited: the next minute will do
                    self.say(f"Discord live status not updated (HTTP {exc.code})", "WARN")
            except Exception:
                pass  # Discord unreachable: try again next minute
            return False


class Notifier:
    def __init__(self, url: str, server: str, switches: dict, say, opener=urllib.request.urlopen):
        self.url, self.server, self.switches, self.say = url.strip(), server, switches, say
        self.opener = opener
        self.q: queue.Queue = queue.Queue(maxsize=100)
        self.recent: dict[str, float] = {}
        if self.url:
            threading.Thread(target=self._loop, daemon=True, name="discord").start()

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def send(self, kind: str, title: str, text: str = "", tone: str = "info") -> bool:
        """Queue an alert. Returns False when it was filtered out."""
        if not self.url or not self.switches.get(SWITCH.get(kind, ""), True):
            return False
        if kind in ("start", "stop") and self.switches.get("live_status"):
            return False  # the live status message already shows online / offline / restarts
        key = f"{kind}|{title}|{text}"
        now = time.time()
        if now - self.recent.get(key, 0) < DEDUPE_SECS:
            return False
        self.recent[key] = now
        try:
            self.q.put_nowait(payload(self.server, title, text, tone))
        except queue.Full:
            return False
        return True

    def _loop(self) -> None:
        while True:
            body = self.q.get()
            self._post(body)

    def _post(self, body: dict, attempts: int = 3) -> None:
        data = json.dumps(body).encode()
        for _ in range(attempts):
            req = urllib.request.Request(self.url, data=data, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "dune-awakening-ptero"})
            try:
                with self.opener(req, timeout=10):
                    return
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    try:
                        wait = float(json.loads(exc.read() or b"{}").get("retry_after", 2))
                    except ValueError:
                        wait = 2.0
                    time.sleep(min(wait, 30))
                    continue
                self.say(f"Discord rejected an alert (HTTP {exc.code}) — check the webhook URL", "WARN")
                return
            except Exception:
                time.sleep(2)
        self.say("Discord unreachable — an alert was dropped", "WARN")
