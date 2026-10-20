"""Start, watch and stop the battlegroup's processes.

The core start scripts launch each service with `setsid` and record its pid in
runtime/pids/<name>.pid; game servers (ue5-*) are launched the same way by
mock-k8s. This module only reads those pid files and signals process groups —
it never re-implements how a service is started.

Ordering and grace periods are the ones the previous supervisor (console.sh)
arrived at in production: game servers save to Postgres on exit, so the
database and brokers must outlive them.
"""
from __future__ import annotations

import glob
import os
import signal
import subprocess
import time

EX_CONFIG = 78  # core scripts: "your configuration is wrong" — retrying won't help

# Boot order after prestart + settings. Every entry is a core script that
# returns once its service is ready.
START_SCRIPTS = [
    # apply-refill-queue.sh: base refills queued from the panel, applied before any map loads
    "start-pg.sh", "migrate-db.sh", "apply-refill-queue.sh", "start-mq-admin.sh", "start-mq-game.sh",
    "start-text-router.sh", "start-fls-stub.sh", "start-mock-k8s.sh",
    "start-director.sh", "start-gateway.sh",
]
CRITICAL = ("postgres", "mq-admin", "mq-game", "text-router", "director", "gateway")
OPTIONAL = ("fls-stub", "mock-k8s")
WHY = {
    "postgres": "the database — characters and the world cannot be read or saved",
    "mq-admin": "the internal message broker",
    "mq-game": "the game message broker — game servers and players talk through it",
    "text-router": "broker login checks — without it every broker login is refused "
                   "and the Director reports 'RMQ unreachable'",
    "director": "the Battlegroup Director — logins, travel and map scaling",
    "gateway": "the Funcom gateway — the server disappears from the server browser",
    "fls-stub": "Funcom login relay used by the gateway",
    "mock-k8s": "the instance launcher the Director uses to start maps",
}
# Stop phases: (names, grace seconds). mock-k8s goes first so it cannot start
# replacement game servers while they are being stopped.
STOP_PHASES = [
    (("mock-k8s",), 10),
    # Game servers save characters and the world to the database on exit, and
    # can take a while doing it with players on: kill only if truly stuck.
    (("ue5-*",), 120),
    (("gateway",), 8),
    (("director",), 8),
    (("text-router", "fls-stub"), 8),
    (("mq-game", "mq-admin"), 15),
    (("postgres",), 15),
]
GAME_BINARY = "DuneSandboxServer"


class Services:
    def __init__(self, base: str, env: dict, say):
        self.base, self.env, self.say = base, env, say
        self.stopping = False
        self.scripts = os.path.join(base, "scripts")
        self.pids_dir = os.path.join(base, "runtime", "pids")
        self.logs = os.path.join(base, "logs")

    # ---- running core scripts -------------------------------------------
    def run_script(self, name: str, *args: str, timeout: int | None = None) -> int:
        """Run one core script in the foreground; its output goes to the console."""
        cmd = ["bash", os.path.join(self.scripts, name), self.base, *args]
        try:
            return subprocess.run(cmd, env=self.env, cwd=self.base, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            self.say(f"{name} did not finish within {timeout}s", "ERROR")
            return 124

    def spawn_script(self, name: str) -> subprocess.Popen:
        """Run a core script in the background (e.g. the dimension spawner)."""
        return subprocess.Popen(["bash", os.path.join(self.scripts, name), self.base],
                                env=self.env, cwd=self.base, start_new_session=True)

    # ---- pid bookkeeping -------------------------------------------------
    def pid(self, name: str) -> int | None:
        try:
            with open(os.path.join(self.pids_dir, f"{name}.pid")) as fh:
                raw = fh.read().strip()
        except OSError:
            return None
        if not raw.isdigit():
            return None
        pid = int(raw)
        return pid if alive(pid) else None

    def names(self, pattern: str) -> list[str]:
        if "*" not in pattern:
            return [pattern]
        return sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(self.pids_dir, f"{pattern}.pid")))

    def status(self) -> dict:
        """name -> pid or None, for every known service plus each game server."""
        out = {n: self.pid(n) for n in CRITICAL + OPTIONAL}
        for n in self.names("ue5-*"):
            out[n] = self.pid(n)
        return out

    def game_servers_alive(self) -> int:
        n = sum(1 for name in self.names("ue5-*") if self.pid(name))
        return n or len(game_pids())

    def dead_critical(self) -> list[str]:
        return [n for n in CRITICAL if self.pid(n) is None]

    # ---- stopping --------------------------------------------------------
    def stop_all(self) -> None:
        self.stopping = True
        try:
            self._stop_phases()
        finally:
            self.stopping = False

    def _stop_phases(self) -> None:
        for names, grace in STOP_PHASES:
            targets = {}
            for pattern in names:
                for name in self.names(pattern):
                    pid = self.pid(name)
                    if pid:
                        targets[name] = pid
            if "ue5-*" in names:  # also any game server a pid file missed
                for pid in game_pids():
                    targets.setdefault(f"game-{pid}", pid)
            if not targets:
                continue
            named = sorted(n for n in targets if not n.startswith("game-"))
            extra = len(targets) - len(named)  # helper processes of the same game servers
            self.say(f"stopping {', '.join(named) or 'game servers'}"
                     + (f" (+{extra} game processes)" if extra and named else "") + f" (up to {grace}s)")
            for pid in targets.values():
                signal_group(pid, signal.SIGTERM)
            start = time.time()
            deadline, report = start + grace, start + 30
            while time.time() < deadline and any(alive(p) for p in targets.values()):
                if time.time() >= report:  # say what we are waiting for, so a slow stop is explainable
                    report += 30
                    left = [f"{n} ({proc_name(p)})" if n.startswith("game-") else n
                            for n, p in targets.items() if alive(p)]
                    self.say(f"still waiting after {int(time.time() - start)}s for: {', '.join(left)}")
                time.sleep(0.5)
            if time.time() - start >= 5 and not any(alive(p) for p in targets.values()):
                self.say(f"  stopped after {int(time.time() - start)}s")
            for name, pid in targets.items():
                if alive(pid):
                    self.say(f"{name} ignored the stop request for {grace}s — killing it", "WARN")
                    signal_group(pid, signal.SIGKILL)
        for pid in game_pids():  # last sweep: nothing may survive into the next boot
            signal_group(pid, signal.SIGKILL)

    # ---- helpers from the core -------------------------------------------
    def diagnose(self, name: str) -> str:
        """The core's plain-English explanation for a known fatal log pattern."""
        logf = os.path.join(self.logs, f"{name}.log")
        try:
            res = subprocess.run(
                ["bash", "-c", 'source "$0/diagnose.sh"; diagnose_fatal "$1" "$2"', self.scripts, name, logf],
                env=self.env, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return res.stdout.strip() if res.returncode == 0 else ""

    def rotate_logs(self) -> None:
        subprocess.run(["bash", os.path.join(self.scripts, "rotate-logs.sh"), self.base],
                       env=self.env, capture_output=True, timeout=120)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill(0); it is dead for our purposes.
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def proc_name(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm") as fh:
            return fh.read().strip()
    except OSError:
        return "?"


def signal_group(pid: int, sig: int) -> None:
    """Signal the process group (setsid made the service its leader), else the pid."""
    try:
        os.killpg(pid, sig)
        return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def game_pids() -> list[int]:
    """Every running game-server process, whatever launched it."""
    pids = []
    for stat in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(stat, "rb") as fh:
                if GAME_BINARY.encode() in fh.read():
                    pids.append(int(stat.split("/")[2]))
        except (OSError, ValueError):
            continue
    return [p for p in pids if p != os.getpid() and alive(p)]
