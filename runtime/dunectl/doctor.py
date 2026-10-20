"""Health checks run at every boot and on demand (console `doctor`).

Each check says what is wrong AND what to do about it. FAIL stops the boot and
the supervisor waits for the fix (restarting cannot repair a bad token or a
too-small container); WARN is printed and the boot continues.

The things checked are the ones that actually broke real servers: a token that
does not decode, the wrong public IP advertised, Wings' default 512 thread limit
(surfaces as a misleading "Out of memory"), a CPU without AVX2, missing game
files, and a runtime image that cannot run updates.
"""
from __future__ import annotations

import base64
import ipaddress
import json
import os
import shutil
import time
import urllib.request

OK, WARN, FAIL = "ok", "warn", "fail"
GiB = 1024 ** 3


class Check:
    def __init__(self, name: str, status: str, message: str, fix: str = ""):
        self.name, self.status, self.message, self.fix = name, status, message, fix

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "message": self.message, "fix": self.fix}

    def lines(self) -> list[str]:
        mark = {OK: "✔", WARN: "!", FAIL: "✘"}[self.status]
        out = [f"{mark} {self.name}: {self.message}"]
        if self.fix and self.status != OK:
            out.append(f"    → {self.fix}")
        return out


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def decode_token(token: str) -> dict | None:
    """Payload of the Funcom self-host JWT, or None. Same decode as prestart.sh."""
    parts = token.strip().split(".")
    if len(parts) < 2:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return None


def public_ip(timeout: float = 5.0) -> str | None:
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                ip = resp.read(64).decode().strip()
            ipaddress.ip_address(ip)
            return ip
        except Exception:
            continue
    return None


def check_token(token: str, now: float) -> Check:
    if not token:
        return Check("Token", FAIL, "no Self-Host Service Token set",
                     "paste your token into DUNE_TOKEN on the Startup tab (get one at account.duneawakening.com)")
    payload = decode_token(token)
    if not payload or not payload.get("HostId"):
        return Check("Token", FAIL, "the token does not decode (no HostId)",
                     "copy the token again from account.duneawakening.com — the whole value, no spaces")
    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp < now:
        return Check("Token", FAIL, f"the token expired on {time.strftime('%Y-%m-%d', time.gmtime(exp))}",
                     "generate a new token at account.duneawakening.com")
    return Check("Token", OK, f"valid (HostId {payload['HostId']})")


def check_ip(configured: str, detected: str | None) -> tuple[Check, str]:
    """(check, ip to advertise)."""
    if configured.lower() in ("", "auto"):
        if not detected:
            return (Check("Public IP", FAIL, "could not detect this server's public IP",
                          "set EXTERNAL_IP on the Startup tab to your public IPv4 address"), "")
        return Check("Public IP", OK, f"{detected} (auto-detected)"), detected
    try:
        ip = ipaddress.ip_address(configured)
    except ValueError:
        return (Check("Public IP", FAIL, f"EXTERNAL_IP '{configured}' is not an IP address",
                      "set it to your public IPv4 address, or to auto"), "")
    if ip.is_private or ip.is_loopback:
        return (Check("Public IP", FAIL, f"{ip} is a private address — players outside your network cannot join",
                      "set EXTERNAL_IP to your public IP (or auto)"), str(ip))
    if detected and detected != str(ip):
        return (Check("Public IP", WARN, f"EXTERNAL_IP is {ip} but this server's public IP looks like {detected}",
                      "if players cannot join, set EXTERNAL_IP to auto"), str(ip))
    return Check("Public IP", OK, str(ip)), str(ip)


def check_threads(read=_read) -> Check:
    raw = read("/sys/fs/cgroup/pids.max") or read("/sys/fs/cgroup/pids/pids.max")
    raw = (raw or "").strip()
    if not raw or raw == "max":
        return Check("Thread limit", OK, "unlimited")
    try:
        limit = int(raw)
    except ValueError:
        return Check("Thread limit", OK, raw)
    fix = ("on the Wings machine set `container_pid_limit: 4096` under `docker:` in "
           "/etc/pterodactyl/config.yml, run `systemctl restart wings`, then press Restart in "
           "Pterodactyl (the console `restart` cannot change this limit)")
    # A warning, not a stop: small setups do run under 512 — but once several
    # maps are up, services start dying with a misleading "Out of memory".
    if limit < 1024:
        return Check("Thread limit", WARN, f"{limit} — too low: with several maps up, services die "
                     "with a misleading 'Out of memory'", fix)
    if limit < 4096:
        return Check("Thread limit", WARN, f"{limit} — may run out with several maps up", fix)
    return Check("Thread limit", OK, str(limit))


def check_memory(read=_read) -> Check:
    raw = (read("/sys/fs/cgroup/memory.max") or read("/sys/fs/cgroup/memory/memory.limit_in_bytes") or "").strip()
    if not raw or raw == "max":
        return Check("Memory", OK, "unlimited")
    try:
        limit = int(raw)
    except ValueError:
        return Check("Memory", OK, raw)
    if limit >= 1 << 60:  # cgroup v1 "unlimited"
        return Check("Memory", OK, "unlimited")
    gib = limit / GiB
    if gib < 24:
        return Check("Memory", WARN, f"limited to {gib:.0f} GiB — a battlegroup needs 32+ GiB",
                     "set Memory to 0 (unlimited) in the server's Build Configuration")
    return Check("Memory", OK, f"{gib:.0f} GiB")


# Rough RAM per running game server and for everything else, measured on real
# servers (a 32 GB container held 4 game servers and ran out at 6).
GAME_SERVER_GIB = 5
BASE_GIB = 4


def memory_limit(read=_read) -> int | None:
    raw = (read("/sys/fs/cgroup/memory.max") or read("/sys/fs/cgroup/memory/memory.limit_in_bytes") or "").strip()
    if raw.isdigit() and int(raw) < 1 << 60:
        return int(raw)
    # Memory 0 (unlimited) in Pterodactyl: the machine's RAM is the real limit.
    for line in (read("/proc/meminfo") or "").splitlines():
        if line.startswith("MemTotal:") and line.split()[1].isdigit():
            return int(line.split()[1]) * 1024
    return None


def check_budget(servers: int, max_concurrent: int, read=_read) -> Check:
    """Will the maps that start at boot fit in the container's memory?"""
    need = servers * GAME_SERVER_GIB + BASE_GIB
    limit = memory_limit(read)
    if limit is None:
        return Check("Map budget", OK, f"{servers} game servers at start (~{need} GiB), memory unlimited")
    have = limit / GiB
    fix = ("lower Deep Desert / Arrakeen / Harko Copies or remove maps from Always-Running Maps "
           "(Startup tab), or lower Max Running Maps — or give the server more memory")
    if need > have:
        return Check("Map budget", WARN, f"{servers} game servers start with the server (~{need} GiB) "
                     f"but the container has {have:.0f} GiB — it will run out of memory", fix)
    spare = int((have - need) // GAME_SERVER_GIB)
    msg = f"{servers} game servers at start (~{need} GiB of {have:.0f} GiB); room for ~{spare} on-demand map(s)"
    if spare < 1:
        return Check("Map budget", WARN, msg + " — players entering dungeons/story maps will run it out", fix)
    if max_concurrent > servers + spare:
        msg += f" (Max Running Maps {max_concurrent} is more than fits: ~{servers + spare})"
    return Check("Map budget", OK, msg)


def check_disk(base: str, usage=shutil.disk_usage) -> Check:
    try:
        free = usage(base).free / GiB
    except OSError:
        return Check("Disk", OK, "unknown")
    if free < 5:
        return Check("Disk", FAIL, f"only {free:.1f} GiB free", "raise the server's Disk limit or delete old backups")
    if free < 15:
        return Check("Disk", WARN, f"{free:.1f} GiB free — game updates need ~15 GiB", "raise the Disk limit")
    return Check("Disk", OK, f"{free:.0f} GiB free")


def check_cpu(read=_read) -> Check:
    info = read("/proc/cpuinfo") or ""
    if not info:
        return Check("CPU", OK, "unknown")
    if " avx2" not in info:
        return Check("CPU", FAIL, "this CPU has no AVX2 — the game server will not run",
                     "move the server to a node with a CPU from ~2013 or newer (AVX2)")
    return Check("CPU", OK, f"{info.count('processor')} threads, AVX2")


def check_install(base: str, exists=os.path.isdir) -> Check:
    missing = [d for d in ("extracted/postgres", "extracted/game-server", "scripts", "server")
               if not exists(os.path.join(base, d))]
    if missing:
        return Check("Game files", FAIL, f"missing {', '.join(missing)}",
                     "run Reinstall on the server (Settings → Reinstall Server)")
    return Check("Game files", OK, "installed")


def check_image(sa_dir: str = "/var/run/secrets/kubernetes.io/serviceaccount",
                isdir=os.path.isdir, writable=lambda p: os.access(p, os.W_OK)) -> Check:
    if not isdir(sa_dir) or not writable(sa_dir):
        return Check("Docker image", FAIL, "this image cannot run the server (no writable service-account folder)",
                     "select this egg's Docker image on the Startup tab")
    return Check("Docker image", OK, "compatible")


def check_updates(base: str, exists=os.path.exists, which=shutil.which) -> Check:
    loader = exists("/lib/ld-linux.so.2") or exists(os.path.join(base, "tools/lib32/ld-linux.so.2"))
    patchelf = bool(which("patchelf")) or exists(os.path.join(base, "tools/bin/patchelf"))
    if loader and patchelf:
        return Check("Game updates", OK, "available without Reinstall")
    return Check("Game updates", WARN, "this install cannot update in place",
                 "run Reinstall once — it provides the update tools")


def run(base: str, sv: dict, config_problems: list[str], port_problems: list[str],
        *, detect_ip=public_ip, now: float | None = None, boot_servers: int = 0,
        max_concurrent: int = 8) -> tuple[list[Check], str]:
    """All checks, plus the IP to advertise ("" when it could not be decided)."""
    checks: list[Check] = []
    if config_problems:
        checks.append(Check("Config", FAIL, f"{len(config_problems)} problem(s) in config/dune.toml:\n      "
                            + "\n      ".join(config_problems),
                            "fix the lines above in the File Manager, then type `restart`"))
    else:
        checks.append(Check("Config", OK, "config/dune.toml is valid"))
    checks.append(check_token(sv["token"], time.time() if now is None else now))
    # Detected even when EXTERNAL_IP is set: a mismatch is worth a warning.
    ip_check, ip = check_ip(sv["external_ip"], detect_ip())
    checks.append(ip_check)
    if port_problems:
        checks.append(Check("Ports", FAIL, "; ".join(port_problems), "see config/dune.toml [ports]"))
    checks += [check_threads(), check_memory(), check_budget(boot_servers, max_concurrent),
               check_disk(base), check_cpu(),
               check_install(base), check_image(), check_updates(base)]
    return checks, ip


def fatal(checks: list[Check]) -> bool:
    return any(c.status == FAIL for c in checks)
