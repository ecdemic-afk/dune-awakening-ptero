"""Built-in schedule: restarts at set times, backups every N hours, announcements.

Runs inside the supervisor (no Pterodactyl API key, unlike the old scheduler).
Stored in server/state/panel-schedule.json and edited from the web panel. Times
are the server's local time; the panel shows the server clock next to them.
"""
from __future__ import annotations

import json
import os
import re
import time

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DEFAULT = {"restarts": [], "backup_every_hours": 0, "announcements": []}


def path(base: str) -> str:
    return os.path.join(base, "server", "state", "panel-schedule.json")


def load(base: str) -> dict:
    try:
        with open(path(base), encoding="utf-8") as fh:
            data = json.load(fh)
        return validate(data)
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULT))


def _days(raw) -> list[str]:
    days = [str(d).lower()[:3] for d in (raw or [])]
    bad = [d for d in days if d not in DAYS]
    if bad:
        raise ValueError(f"unknown day '{bad[0]}'")
    return [d for d in DAYS if d in days]  # every day when empty


def _time(raw) -> str:
    t = str(raw or "").strip()
    if len(t) == 4 and t[1] == ":":
        t = "0" + t
    if not HHMM.match(t):
        raise ValueError(f"'{raw}' is not a time like 04:00")
    return t


def validate(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("the schedule must be an object")
    out = {"restarts": [], "announcements": []}
    for r in raw.get("restarts") or []:
        warn = int(r.get("warn_minutes", 10))
        if not 0 <= warn <= 60:
            raise ValueError("countdown must be 0-60 minutes")
        out["restarts"].append({"time": _time(r.get("time")), "days": _days(r.get("days")),
                                "warn_minutes": warn, "update": bool(r.get("update", False)),
                                "reason": str(r.get("reason") or "Scheduled restart")[:80]})
    for a in raw.get("announcements") or []:
        msg = str(a.get("message") or "").strip()
        if not msg:
            raise ValueError("an announcement needs a message")
        out["announcements"].append({"time": _time(a.get("time")), "days": _days(a.get("days")), "message": msg[:200]})
    hours = int(raw.get("backup_every_hours") or 0)
    if not 0 <= hours <= 168:
        raise ValueError("backups: every 1-168 hours, or 0 for off")
    out["backup_every_hours"] = hours
    if len(out["restarts"]) > 24 or len(out["announcements"]) > 48:
        raise ValueError("too many entries")
    return out


def save(base: str, raw) -> dict:
    data = validate(raw)
    p = path(base)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(p + ".tmp", p)
    return data


def due(cfg: dict, now: time.struct_time, fired: set) -> list[tuple[str, str, dict]]:
    """Entries whose minute is now. Returns (kind, key, entry); key goes in `fired`
    so one minute never triggers twice."""
    hhmm = f"{now.tm_hour:02d}:{now.tm_min:02d}"
    day = DAYS[now.tm_wday]
    stamp = time.strftime("%Y-%m-%d", now)
    out = []
    for kind in ("restarts", "announcements"):
        for i, e in enumerate(cfg.get(kind, [])):
            key = f"{kind}:{i}:{stamp}:{e['time']}"
            if e["time"] == hhmm and (not e["days"] or day in e["days"]) and key not in fired:
                out.append((kind, key, e))
    return out


def backup_due(cfg: dict, newest_backup_mtime: float | None, now: float) -> bool:
    hours = cfg.get("backup_every_hours") or 0
    if not hours:
        return False
    return newest_backup_mtime is None or now - newest_backup_mtime >= hours * 3600 - 60
