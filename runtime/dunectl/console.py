"""Console command parsing (pure; the supervisor executes the result).

Commands are typed in Pterodactyl's Console tab or sent by a Pterodactyl
Schedule ("Send command" task) — e.g. a nightly `restart in 10m Nightly restart`.
"""
from __future__ import annotations

import re
import shlex

HELP = """\
Commands (type them in this console, or add them to a Pterodactyl Schedule):
  status                     what is running, players online, ports
  doctor                     health check with fixes
  players                    list players (online first)
  announce <message>         in-game banner to everyone
  restart [in 10m] [reason]  restart the server (players get a countdown)
  restart now                restart immediately
  update [now]               install a game update, then restart
  backup                     save the database to backups/ (do this before
                             a Pterodactyl backup — see the README)
  restore                    list database backups
  restore <file> confirm     restore one (the current world is backed up first)
  upgrade [now]              install the newest version of this server software
                             (downloads it, then restarts — no Reinstall)
  cancel                     cancel a pending restart or update
  stop                       stop everything (same as Pterodactyl's Stop)
  config                     check config/dune.toml for mistakes
  ports                      which ports to allocate and forward
  logs verbose|quiet         show all service output, or just warnings
  admin <op> [args...]       run a raw game operation (advanced)"""

DURATION = re.compile(r"^(\d+)\s*(s|sec|secs|m|min|mins|h|hr|hrs)?$", re.I)


def parse_duration(text: str) -> int | None:
    """'10m' -> 600, '90s' -> 90, '1h' -> 3600, '15' -> 900 (minutes). None if not a duration."""
    m = DURATION.match(text.strip())
    if not m:
        return None
    n, unit = int(m.group(1)), (m.group(2) or "m").lower()
    return n * {"s": 1, "m": 60, "h": 3600}[unit[0]]


def human(secs: int) -> str:
    if secs >= 3600 and secs % 3600 == 0:
        return f"{secs // 3600}h"
    if secs >= 60 and secs % 60 == 0:
        return f"{secs // 60} min"
    return f"{secs}s"


class Command:
    def __init__(self, name: str, **kw):
        self.name = name
        self.__dict__.update(kw)

    def __repr__(self):
        return f"Command({self.__dict__})"

    def __eq__(self, other):
        return isinstance(other, Command) and self.__dict__ == other.__dict__


def parse(line: str, default_warn_secs: int) -> Command:
    line = line.strip().lstrip("/")
    if not line:
        return Command("noop")
    try:
        words = shlex.split(line)
    except ValueError:
        words = line.split()
    cmd, args = words[0].lower(), words[1:]

    if cmd in ("help", "?", "commands"):
        return Command("help")
    if cmd in ("status", "doctor", "players", "config", "ports", "cancel", "stop", "backup"):
        return Command(cmd)
    if cmd == "announce":
        text = line.split(None, 1)[1] if len(words) > 1 else ""
        return Command("announce", text=text) if text else Command("error", text="usage: announce <message>")
    if cmd in ("restart", "update", "upgrade"):
        delay, reason_words = None, args
        if args and args[0].lower() == "now":
            delay, reason_words = 0, args[1:]
        elif len(args) >= 2 and args[0].lower() == "in":
            delay = parse_duration(args[1])
            if delay is None:
                return Command("error", text=f"'{args[1]}' is not a duration — use e.g. 10m, 90s, 1h")
            reason_words = args[2:]
        elif args and parse_duration(args[0]) is not None:
            delay, reason_words = parse_duration(args[0]), args[1:]
        return Command(cmd, delay=default_warn_secs if delay is None else delay,
                       explicit=delay is not None, reason=" ".join(reason_words))
    if cmd == "restore":
        if not args:
            return Command("restore", file="", confirmed=False)
        return Command("restore", file=args[0], confirmed=len(args) > 1 and args[1].lower() == "confirm")
    if cmd == "logs":
        if args and args[0].lower() in ("verbose", "quiet"):
            return Command("logs", verbose=args[0].lower() == "verbose")
        return Command("error", text="usage: logs verbose|quiet")
    if cmd == "admin":
        return Command("admin", argv=args) if args else Command("error", text="usage: admin <operation> [args...]")
    return Command("unknown", text=cmd)
