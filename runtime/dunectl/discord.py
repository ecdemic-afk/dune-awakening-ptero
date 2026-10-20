"""Discord webhook alerts.

Sent from a background thread so a slow or unreachable Discord can never hold
up the server. Identical alerts within 10 minutes are sent once (a crash loop
must not flood the channel), and Discord's 429 rate limit is honoured.
The webhook URL is a secret: it is never printed.
"""
from __future__ import annotations

import json
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
