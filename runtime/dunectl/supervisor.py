"""The container's main process.

Boot: config → health checks → (game update) → core boot stages → watch.
Because this process owns every service, a restart or update is done in place:
stop everything, start everything — no Pterodactyl API key, no container
recreation, and the console keeps its history.

Failure policy (ported from the previous console.sh, which earned it in
production): a core service down for `core_down_grace` seconds or every game
server down for `game_down_grace` seconds triggers an automatic restart, unless
the core recognises a configuration refusal — then restarting would only replay
it, so the supervisor waits ("held") for the operator. More than
`max_auto_restarts` restarts in 30 minutes also holds, instead of looping.
"""
from __future__ import annotations

import base64
import collections
import itertools
import os
import signal
import subprocess
import sys
import threading
import time

from . import __version__
from . import config as configmod
from . import configform
from . import console, dims, doctor, gameplay, ports, upgrade
from .discord import Notifier
from .logstream import Follower
from .panel import Panel
from .players import PlayerTools
from .world import WorldTools
from .images import ItemImages
from . import schedule as schedmod
from .services import CRITICAL, EX_CONFIG, OPTIONAL, START_SCRIPTS, WHY, Services

CRASH_WINDOW = 1800
TICK = 5
ROTATE_EVERY = 300

_print_lock = threading.Lock()
# What the console showed, for the web panel: (seq, epoch, tag, level, line).
LOG: collections.deque = collections.deque(maxlen=3000)
_seq = itertools.count(1)


def say(msg: str, level: str = "INFO", tag: str = "dune") -> None:
    prefix = f"[{tag}]" if level == "INFO" else f"[{tag}] [{level}]"
    with _print_lock:
        for i, line in enumerate(str(msg).splitlines() or [""]):
            print(f"{prefix} {line}" if i == 0 else f"{prefix}   {line}", flush=True)
            LOG.append((next(_seq), time.time(), tag, level, line))


def restore_succeeded(rc: int, output: str) -> bool:
    """pg_restore --clean exits 1 on a world with partitioned tables: it tries to drop
    each partition's primary key on its own, which Postgres refuses (the key belongs
    to the parent and goes with it). Those refusals are the only errors that are safe."""
    if rc == 0:
        return True
    errors = [l for l in output.splitlines() if "pg_restore: error:" in l]
    return bool(errors) and all("cannot drop inherited constraint" in l for l in errors)


class Held(Exception):
    """Boot cannot continue until the operator fixes something."""


class Supervisor:
    def __init__(self, base: str):
        self.base = base
        self.scripts = os.path.join(base, "scripts")
        self.state = "starting"
        self.action_lock = threading.Lock()
        self.pending = None           # (kind, at_epoch, reason) for a countdown
        self.cancel_evt = threading.Event()
        self.stop_evt = threading.Event()
        self.crashes: list[float] = []
        self.started_at = time.time()
        self.cfg: dict = configmod.defaults()
        self.sv = configmod.startup_vars(os.environ)
        self.layout: dict = {}
        self.env: dict = dict(os.environ)
        self.svc = Services(base, self.env, self._svc_say)
        self.discord = Notifier(self.sv["discord_webhook"], self.sv["server_name"],
                                self.cfg["discord"], say)
        self.follower: Follower | None = None
        if self.scripts not in sys.path:
            sys.path.insert(0, self.scripts)  # core helpers (admin_ban, admin_progression, ...)
        self.players = PlayerTools(base, self.run_op, say)
        self.world = WorldTools(base, self.scripts, self.run_op, say)
        self.images = ItemImages(base, self.players.category_of)
        self._fired: set = set()
        self._sched_last = 0.0
        self._backup_running = False
        self.watch_since: dict[str, float] = {}
        self.reported: set[str] = set()

    def _svc_say(self, msg, level="INFO"):
        say(msg, level)

    # ------------------------------------------------------------------ boot
    def prepare(self, apply: bool = True) -> list[doctor.Check]:
        """Config + health. Raises Held on a fatal problem. apply=False only
        reports (console `doctor` while running must not swap the environment
        the running services were started with)."""
        if configmod.seed(self.base):
            say("Created config/dune.toml with the defaults — edit it in the File Manager anytime.")
        self.cfg, problems = configmod.load(self.base, self.scripts, os.environ)
        for item in self.cfg.pop("_startup_applied", []):
            say(f"Startup tab: {item}")
        self.sv = configmod.startup_vars(os.environ)
        self.discord.switches = self.cfg["discord"]
        port_problems = []
        if not self.sv["server_port"].isdigit():
            port_problems.append("SERVER_PORT is missing — this server has no primary allocation")
            self.layout = {}
        else:
            self.layout = ports.layout(int(self.sv["server_port"]), self.cfg["ports"],
                                       configmod.dimension_count(self.cfg))
            port_problems += ports.problems(self.layout)
        checks, ip = doctor.run(
            self.base, self.sv, problems, port_problems,
            boot_servers=len(self.cfg["instances"]["always_warm"]) + configmod.dimension_count(self.cfg),
            max_concurrent=self.cfg["instances"]["max_concurrent"])
        self.last_checks = [c.as_dict() for c in checks]
        for c in checks:
            for line in c.lines():
                say(line, "INFO" if c.status == doctor.OK else ("WARN" if c.status == doctor.WARN else "ERROR"))
        if self.layout:
            for line in ports.describe(self.layout):
                say(line)
        if doctor.fatal(checks):
            raise Held("fix the ✘ items above")
        if not apply:
            return checks
        self.env = dict(os.environ)
        self.env.update(configmod.core_env(self.cfg, self.sv, ip, self.layout))
        self.env["DUNE_BASE_DIR"] = self.base
        self.env["AMP_TOKEN"] = self._sa_token()
        self.svc = Services(self.base, self.env, self._svc_say)
        for c in checks:
            if c.status == doctor.WARN:
                self.discord.send("health", f"Health warning: {c.name}", f"{c.message}\n{c.fix}", "warn")
        return checks

    def _sa_token(self) -> str:
        path = os.path.join(self.base, "server", "state", "sa-token")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w") as fh:
                fh.write("ServerId=" + base64.b64encode(os.urandom(32)).decode())
        with open(path) as fh:
            return fh.read().strip()

    def _stage(self, name: str, *args: str) -> None:
        rc = self.svc.run_script(name, *args)
        if rc == EX_CONFIG:
            raise Held(f"{name} refused the configuration — see the error above")
        if rc != 0:
            raise RuntimeError(f"{name} failed (exit {rc}) — see logs/ and the lines above")

    def boot(self, update: bool = False) -> None:
        self.state = "starting"
        self.boot_started = time.time()
        self.joinable = False
        self.prepare()
        if update or self._auto_update_wanted():
            self._update_game(forced=update)
        say("Preparing the world...")
        self._stage("prestart.sh")
        sys.path.insert(0, self.scripts)
        import admin_ini_merge
        stats = gameplay.apply(self.base, self.cfg, self.sv, configmod.load_schema(self.scripts),
                               admin_ini_merge, say)
        say(f"Settings applied: {stats['applied']}" + (f", {stats['failed']} failed" if stats["failed"] else ""))
        self._stage("patch-world-template.sh")
        for script in START_SCRIPTS:
            self._stage(script)
        self._reconcile_dimensions()
        self.svc.spawn_script("start-ue5-dimensions.sh")
        self.state = "running"
        self.started_at = time.time()
        self.watch_since.clear()
        self.reported.clear()
        say("READY — the battlegroup is up; the maps are loading (usually a few minutes). "
            "A 'Players can join' line follows when they can.")
        self.discord.send("start", "Server online", f"{self.sv['server_name']} is starting its maps.", "good")

    def _reconcile_dimensions(self) -> None:
        """Park map copies above the configured counts so they stay cold (see dims.py)."""
        sql = ("SELECT partition_id, map, dimension_index FROM dune.world_partition "
               "WHERE dimension_index > 0 ORDER BY map, dimension_index")
        try:
            res = subprocess.run(["bash", os.path.join(self.scripts, "admin-publish.sh"), "db-sql", sql],
                                 env=self.env, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            res = None
        if res is None or res.returncode != 0:
            say("could not read the map copies — starting all of them", "WARN")
            return
        park, unpark = dims.plan(dims.parse_rows(res.stdout), self.cfg)
        import admin_park  # core: the parked set start-ue5-dimensions.sh skips
        for pid in sorted(park):
            if not admin_park.is_parked(self.base, pid):
                admin_park.park(self.base, pid)
        for pid in sorted(unpark):
            if admin_park.is_parked(self.base, pid):
                admin_park.unpark(self.base, pid)
        if park:
            say(f"Map copies kept cold (above the counts in [instances]): partitions "
                f"{', '.join(map(str, sorted(park)))}")

    def _auto_update_wanted(self) -> bool:
        flag = self.sv["auto_update"].lower()
        if flag in ("1", "true", "yes", "on"):
            return True
        if flag in ("0", "false", "no", "off"):
            return False
        return bool(self.cfg["updates"]["check_on_start"])

    def _update_game(self, forced: bool) -> None:
        marker = os.path.join(self.base, "server", "state", "update-requested")
        if forced:
            open(marker, "a").close()
        env = dict(self.env, AUTO_UPDATE="1")
        say("Checking for a game update...")
        rc = subprocess.run(["bash", os.path.join(self.scripts, "update-game.sh"), self.base],
                            env=env, cwd=self.base).returncode
        if rc != 0:
            say("Game update did not complete — starting the installed version (details above)", "WARN")
            self.discord.send("update", "Game update failed", "The installed version is running. See the console.", "warn")
        elif forced:
            self.discord.send("update", "Game updated", "The new game version is installed.", "good")

    # -------------------------------------------------------------- lifecycle
    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        say(f"dune-awakening-ptero {__version__}")
        threading.Thread(target=self._stdin_loop, daemon=True, name="console").start()
        # While stopping, every service complains about the ones stopped before it.
        self.follower = Follower(os.path.join(self.base, "logs"),
                                 lambda t, m, lv: None if self.svc.stopping else say(m, lv, t))
        self.follower.start()
        self._start_panel()
        self._boot_until_up()
        last_rotate = 0.0
        while not self.stop_evt.wait(TICK):
            if time.time() - last_rotate > ROTATE_EVERY:
                last_rotate = time.time()
                try:
                    self.svc.rotate_logs()
                except Exception:
                    pass
            if self.state == "running" and not self.action_lock.locked():
                self._watch()
                self._schedule_tick()
        return 0

    def _boot_until_up(self, update: bool = False) -> None:
        """Boot; on a config problem wait for a fix and retry by itself."""
        while not self.stop_evt.is_set():
            try:
                self.boot(update=update)
                return
            except Held as exc:
                self._hold(str(exc))
            except Exception as exc:
                say(f"Start failed: {exc}", "ERROR")
                self.svc.stop_all()
                if not self._record_crash(f"Start failed: {exc}"):
                    self._hold("the server failed to start several times in a row")
                elif self.stop_evt.wait(15):  # don't retry a fast failure in a tight loop
                    return
            update = False

    def _hold(self, why: str) -> None:
        """Wait for the operator, re-checking when config/dune.toml changes."""
        self.state = "held"
        self.svc.stop_all()
        say("──────────────────────────────────────────────────────────────", "WARN")
        say(f"WAITING FOR YOU: {why}.", "WARN")
        say("Nothing restarts until it is fixed. After fixing, type `restart` here", "WARN")
        say("(a saved change to config/dune.toml is picked up automatically). For a", "WARN")
        say("Startup-tab or Wings change, use Pterodactyl's Restart button instead.", "WARN")
        say("──────────────────────────────────────────────────────────────", "WARN")
        self.discord.send("crash", "Server waiting for an admin", why, "bad")
        cfg_path = configmod.config_path(self.base)
        mtime = os.path.getmtime(cfg_path) if os.path.exists(cfg_path) else 0
        self.cancel_evt.clear()
        reminded = time.time()
        while not self.stop_evt.is_set():
            if self.cancel_evt.wait(5):  # `restart` typed
                break
            now_m = os.path.getmtime(cfg_path) if os.path.exists(cfg_path) else 0
            if now_m != mtime:
                say("config/dune.toml changed — trying again")
                break
            if time.time() - reminded > 300:
                reminded = time.time()
                say(f"still waiting: {why}", "WARN")
        self.cancel_evt.clear()
        self.crashes.clear()

    def _record_crash(self, what: str) -> bool:
        """True when another automatic restart is allowed."""
        now = time.time()
        self.crashes = [t for t in self.crashes if now - t < CRASH_WINDOW] + [now]
        limit = self.cfg["advanced"]["max_auto_restarts"]
        self.discord.send("crash", "Server problem", what, "bad")
        return len(self.crashes) <= limit

    # ------------------------------------------------------------------ watch
    def _check_joinable(self, now: float) -> None:
        """Say once when Survival_1 (where players spawn) has registered as ready."""
        if getattr(self, "joinable", True) or now - self.watch_since.get("joinable", 0) < 15:
            return
        self.watch_since["joinable"] = now
        sql = "SELECT map FROM dune.farm_state WHERE ready AND alive"
        try:
            res = subprocess.run(["bash", os.path.join(self.scripts, "admin-publish.sh"), "db-sql", sql],
                                 env=self.env, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return
        maps = {l.strip() for l in res.stdout.splitlines()[1:]}
        if "Survival_1" in maps:
            self.joinable = True
            secs = int(now - self.boot_started)
            took = f"{secs // 60}m{secs % 60:02d}s"
            say(f"Players can join — Survival_1 is up, {took} after start. Maps up: {', '.join(sorted(maps))}")
            self.discord.send("start", "Players can join", f"Ready {took} after start.", "good")

    def _watch(self) -> None:
        adv = self.cfg["advanced"]
        now = time.time()
        self._check_joinable(now)
        status = self.svc.status()
        if not any(status.values()):
            return self._crash("every service has stopped")
        for name in OPTIONAL:
            if status.get(name) is None and name not in self.reported:
                self.reported.add(name)
                say(f"{name} is not running — {WHY[name]}. See logs/{name}.log", "WARN")
            elif status.get(name) and name in self.reported:
                self.reported.discard(name)
                say(f"{name} is running again")
        dead = [n for n in CRITICAL if status.get(n) is None]
        if dead:
            since = self.watch_since.setdefault("critical", now)
            if now - since >= adv["core_down_grace"]:
                explained = []
                for n in dead:
                    say(f"{n} is down — {WHY[n]}. See logs/{n}.log", "ERROR")
                    d = self.svc.diagnose(n)
                    if d:
                        say(d, "ERROR")
                        explained.append(n)
                if explained:
                    self.svc.stop_all()
                    self._hold(f"{', '.join(explained)} refused the configuration (see above)")
                    return self._restart_now("configuration fixed")
                return self._crash(f"{', '.join(dead)} stopped")
        else:
            self.watch_since.pop("critical", None)
        # Game servers take minutes to come up; only judge them after a grace.
        if now - self.started_at > adv["game_down_grace"] and self.svc.game_servers_alive() == 0:
            since = self.watch_since.setdefault("game", now)
            if now - since >= adv["game_down_grace"]:
                return self._crash(f"no game server has been running for {adv['game_down_grace']}s")
        else:
            self.watch_since.pop("game", None)

    def _crash(self, what: str) -> None:
        say(f"Problem: {what}", "ERROR")
        if self._record_crash(what):
            say("Restarting everything automatically...", "WARN")
            self._restart_now(what)
        else:
            self._hold(f"{len(self.crashes)} automatic restarts in 30 minutes ({what})")
            self._restart_now("operator")

    # ---------------------------------------------------------------- actions
    def _restart_now(self, reason: str, update: bool = False, staged: str = "") -> None:
        # Non-blocking: a crash noticed while a restart is already running must
        # not queue a second one behind it.
        if not self.action_lock.acquire(blocking=False):
            return
        try:
            self.state = "restarting"
            say(f"Restarting ({reason})...")
            self.svc.stop_all()
            if staged:
                self._swap_and_reexec(staged)  # does not return on success
            self._boot_until_up(update=update)
        finally:
            self.action_lock.release()

    def _swap_and_reexec(self, staged: str) -> None:
        try:
            upgrade.swap(self.base, staged)
        except OSError as exc:
            say(f"upgrade could not replace the files ({exc}) — starting the current version", "ERROR")
            return
        say("New version installed — starting it.")
        self.discord.send("update", "Server software upgraded", "", "good")
        if self.follower:
            self.follower.stop()
        sys.stdout.flush()
        os.execv(sys.executable, [sys.executable, os.path.join(self.base, "runtime", "dunectl.py"), "run"])

    def start_upgrade(self, delay: int, reason: str) -> None:
        """Download + stage while the server runs; then a normal countdown restart swaps it in."""
        def work():
            repo = os.environ.get("SOURCE_REPO", "ecdemic-afk/dune-awakening-ptero").strip()
            ref = os.environ.get("SOURCE_REF", "main").strip()
            say(f"Downloading {repo}@{ref}...")
            try:
                staged = upgrade.stage(self.base, upgrade.download(repo, ref))
            except Exception as exc:
                msg = str(exc)
                if "404" in msg:
                    msg += " — check Source Repository and Source Version on the Startup tab"
                say(f"upgrade download failed: {msg}", "ERROR")
                return
            if upgrade.mock_k8s_changed(self.base, staged):
                say("This version also changes the Kubernetes stand-in (mock-k8s); that part only "
                    "updates with a Reinstall. Everything else upgrades now.", "WARN")
            say(self.schedule("upgrade", delay, reason, staged=staged))
        threading.Thread(target=work, daemon=True, name="upgrade").start()

    def players_online(self) -> int | None:
        try:
            res = subprocess.run(["bash", os.path.join(self.scripts, "admin-publish.sh"), "farm-player-count"],
                                 env=self.env, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if res.returncode != 0:
            return None
        total = 0
        for line in res.stdout.splitlines()[1:]:
            tail = line.rsplit(",", 1)[-1].strip()
            if tail.isdigit():
                total += int(tail)
        return total

    def schedule(self, kind: str, delay: int, reason: str, staged: str = "") -> str:
        """Restart/update after an in-game countdown (skipped when nobody is on)."""
        if self.pending:
            return f"a {self.pending[0]} is already pending — type `cancel` first"
        if self.state != "running":
            delay = 0
        else:
            online = self.players_online()
            if delay and online == 0:
                say("Nobody is online — no countdown needed.")
                delay = 0
        self.pending = (kind, time.time() + delay, reason)
        self.cancel_evt.clear()
        threading.Thread(target=self._countdown, args=(kind, delay, reason, staged), daemon=True).start()
        return (f"{kind} in {console.human(delay)}" if delay else f"{kind} now") + (f" — {reason}" if reason else "")

    def _countdown(self, kind: str, delay: int, reason: str, staged: str = "") -> None:
        if delay:
            stype = "Update" if kind == "update" else "Restart"
            self.admin(["shutdown", stype, str(delay), str(max(60, min(300, delay // 5)))], quiet=True)
            self.discord.send("update" if kind == "update" else "start",
                              f"{kind.capitalize()} in {console.human(delay)}", reason or "", "info")
            if self.cancel_evt.wait(delay):
                self.pending = None
                self.admin(["shutdown", "cancel"], quiet=True)
                say(f"{kind} cancelled")
                return
        self.pending = None
        self._restart_now(reason or kind, update=(kind == "update"), staged=staged)
        self.discord.send("update" if kind == "update" else "start",
                          "Update finished" if kind == "update" else "Restart finished",
                          reason or "", "good")

    def backup(self) -> bool:
        """pg_dump into backups/ (consistent while running), keeping [backups] keep."""
        if self.state != "running":
            say("backup needs the server running (the database must be up)", "WARN")
            return False
        say("Backing up the database...")
        ok = self.admin(["db-backup", str(self.cfg["backups"]["keep"])], timeout=1800) == 0
        self.discord.send("backup", "Backup " + ("saved" if ok else "FAILED"),
                          "Database saved to backups/." if ok else "See the console.", "good" if ok else "bad")
        return ok

    def list_backups(self) -> list[str]:
        d = os.path.join(self.base, "backups")
        try:
            names = [n for n in os.listdir(d) if n.endswith(".dump")]
        except OSError:
            return []
        return sorted(names, reverse=True)

    def restore(self, file: str, confirmed: bool) -> None:
        backups = self.list_backups()
        if not file:
            if not backups:
                say("No database backups yet — type `backup` to make one.")
                return
            say("Database backups (newest first):\n" + "\n".join(backups[:20]))
            say("Restore one with: restore <file> confirm")
            return
        if os.path.basename(file) not in backups:
            say(f"no backup named {file} — type `restore` for the list", "WARN")
            return
        if not confirmed:
            say(f"This replaces the whole world with {file}. Everyone is disconnected.\n"
                f"The current world is backed up first. To go ahead: restore {file} confirm", "WARN")
            return
        threading.Thread(target=self._restore, args=(os.path.basename(file),), daemon=True).start()

    def _restore(self, file: str) -> None:
        if not self.action_lock.acquire(blocking=False):
            say("busy with another restart — try again in a moment", "WARN")
            return
        try:
            self._restore_locked(file)
        finally:
            self.action_lock.release()

    def _restore_locked(self, file: str) -> None:
        self.state = "restoring"
        if self.players_online():
            self.admin(["broadcast", "Server", "The server is restoring a backup and restarts now.", "15"], quiet=True)
        say(f"Restoring {file}: stopping the server...")
        self.svc.stop_all()
        try:
            # Database only: the restore must not race a running game server.
            self._stage("prestart.sh")
            self._stage("start-pg.sh")
            say("Saving the current world first...")
            # Retention must not prune the backup we are about to restore.
            keep = max(self.cfg["backups"]["keep"], len(self.list_backups())) + 1
            if self.admin(["db-backup", str(keep)], timeout=1800) != 0:
                raise RuntimeError("could not back up the current world — restore cancelled")
            say(f"Restoring {file}...")
            rc = self.admin(["db-restore", file, "RESTORE"], quiet=True, timeout=1800, echo_failure=False)
            if not restore_succeeded(rc, self.last_output):
                for line in self.last_output.splitlines():
                    if line.strip():
                        say(line, "WARN", "admin")
                raise RuntimeError("the restore failed — the previous world is in the newest backup")
            say(f"Restored {file}.")
            self.discord.send("backup", "Backup restored", file, "good")
        except (RuntimeError, Held) as exc:
            say(str(exc), "ERROR")
            self.discord.send("backup", "Restore failed", str(exc), "bad")
        self.svc.stop_all()
        self._boot_until_up()

    def run_op(self, argv: list[str], timeout: int = 60) -> tuple[int, str, str]:
        """One core operation, output returned rather than printed (for the panel)."""
        try:
            res = subprocess.run(["bash", os.path.join(self.scripts, "admin-publish.sh"), *argv],
                                 env=self.env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", f"{argv[0]} did not finish within {timeout}s"
        except OSError as exc:
            return 127, "", str(exc)
        return res.returncode, res.stdout, res.stderr

    def admin(self, argv: list[str], quiet: bool = False, timeout: int = 120, echo_failure: bool = True) -> int:
        self.last_output = ""
        try:
            res = subprocess.run(["bash", os.path.join(self.scripts, "admin-publish.sh"), *argv],
                                 env=self.env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            say(f"{argv[0]} did not finish within {timeout}s", "ERROR", "admin")
            return 124
        self.last_output = res.stdout + res.stderr
        if not quiet or (res.returncode != 0 and echo_failure):
            for line in (res.stdout + res.stderr).splitlines():
                if line.strip():
                    say(line, "INFO" if res.returncode == 0 else "WARN", "admin")
        return res.returncode

    def shutdown(self) -> None:
        if self.stop_evt.is_set():
            return
        self.stop_evt.set()
        self.cancel_evt.set()
        self.state = "stopping"
        say("Stopping — game servers save first, then the database.")
        self.svc.stop_all()
        self.discord.send("stop", "Server stopped", "", "info")
        time.sleep(0.5)
        say("Stopped.")

    def _on_signal(self, signum, frame) -> None:
        threading.Thread(target=self._exit_after_shutdown, daemon=False).start()

    def _exit_after_shutdown(self) -> None:
        self.shutdown()
        os._exit(0)

    # ---------------------------------------------------------------- console
    def _stdin_loop(self) -> None:
        for raw in sys.stdin:
            try:
                self.handle(raw)
            except Exception as exc:  # a command must never kill the console
                say(f"command failed: {exc}", "ERROR")

    def handle(self, raw: str) -> None:
        cmd = console.parse(raw, self.cfg["updates"]["warn_minutes"] * 60)
        n = cmd.name
        if n == "noop":
            return
        if n == "help":
            say(console.HELP)
        elif n == "status":
            self._print_status()
        elif n == "doctor":
            self.run_doctor()
        elif n == "config":
            _, problems = configmod.load(self.base, self.scripts, os.environ)
            say("config/dune.toml and the Startup tab are valid." if not problems else
                "Configuration problems:\n" + "\n".join(problems), "INFO" if not problems else "WARN")
        elif n == "ports":
            for line in ports.describe(self.layout) if self.layout else ["no port layout (SERVER_PORT missing)"]:
                say(line)
        elif n in ("restart", "update", "upgrade"):
            if n == "upgrade" and self.state == "held":
                # A newer version may be exactly what fixes the problem.
                self.start_upgrade(0, cmd.reason)
                return
            if self.state == "held":
                self.cancel_evt.set()
                say("Trying again...")
                return
            if self.state != "running":
                say(f"the server is {self.state} — wait for READY, then try again", "WARN")
                return
            if n == "upgrade":
                self.start_upgrade(cmd.delay, cmd.reason)
            else:
                say(self.schedule(n, cmd.delay, cmd.reason))
        elif n == "cancel":
            if self.pending:
                self.cancel_evt.set()
            else:
                say("nothing pending")
        elif n == "stop":
            threading.Thread(target=self._exit_after_shutdown, daemon=False).start()
        elif n == "announce":
            self.admin(["broadcast", "Server", cmd.text, "20"])
        elif n == "players":
            self.admin(["players"])
        elif n == "backup":
            self.backup()
        elif n == "restore":
            if cmd.confirmed and self.state != "running":
                say(f"the server is {self.state} — wait for READY, then try again", "WARN")
                return
            self.restore(cmd.file, cmd.confirmed)
        elif n == "logs":
            if self.follower:
                self.follower.verbose = cmd.verbose
            say("console shows all service output" if cmd.verbose else "console shows warnings and errors only")
        elif n == "admin":
            self.admin(cmd.argv)
        elif n == "error":
            say(cmd.text, "WARN")
        else:
            say(f"unknown command `{cmd.text}` — type `help`", "WARN")

    # ------------------------------------------------------------ schedule
    def _schedule_tick(self) -> None:
        """Panel schedule: restarts / announcements at set times, backups every N hours."""
        now = time.time()
        if now - self._sched_last < 20:  # still lands in every minute
            return
        self._sched_last = now
        cfg = schedmod.load(self.base)
        for kind, key, e in schedmod.due(cfg, time.localtime(now), self._fired):
            self._fired.add(key)
            if kind == "restarts":
                say(f"Scheduled {'update and ' if e['update'] else ''}restart ({e['time']}): {e['reason']}")
                say(self.schedule("update" if e["update"] else "restart", e["warn_minutes"] * 60, e["reason"]))
            else:
                say(f"Scheduled announcement: {e['message']}")
                self.admin(["broadcast", "Server", e["message"], "20"], quiet=True)
        self._welcome_tick(now)
        backups = self.list_backups()
        newest = os.path.getmtime(os.path.join(self.base, "backups", backups[0])) if backups else None
        tried = getattr(self, "_backup_tried", 0.0)
        if not self._backup_running and now - tried > 900 and schedmod.backup_due(cfg, newest, now):
            self._backup_running, self._backup_tried = True, now  # a failing backup retries every 15 min, not every tick
            threading.Thread(target=self._scheduled_backup, daemon=True, name="backup").start()

    def _welcome_tick(self, now: float) -> None:
        """Welcome gift: give the chosen kit once to every player who hasn't had this round."""
        if now - getattr(self, "_welcome_last", 0.0) < 60 or getattr(self, "_welcome_running", False):
            return
        self._welcome_last = now
        if not self.players.welcome()["enabled"]:
            return
        self._welcome_running = True

        def scan():
            try:
                import admin_welcome
                r = admin_welcome.run_scan(self.base)
                if r.get("granted") or r.get("failed"):
                    say(f"Welcome gift: given to {r.get('granted', 0)} player(s)"
                        + (f", {r['failed']} failed (retried next time)" if r.get("failed") else ""))
                elif not r.get("ok"):
                    say(f"Welcome gift: {r.get('error')}", "WARN")
            except Exception as exc:  # never let the gift loop take anything down
                say(f"Welcome gift failed: {exc}", "WARN")
            finally:
                self._welcome_running = False
        threading.Thread(target=scan, daemon=True, name="welcome").start()

    def schedule_view(self) -> dict:
        return {"schedule": schedmod.load(self.base), "server_time": time.strftime("%H:%M"),
                "server_day": schedmod.DAYS[time.localtime().tm_wday], "timezone": time.strftime("%Z")}

    def save_schedule(self, raw, who: str) -> dict:
        saved = schedmod.save(self.base, raw)  # ValueError -> 400 with the reason
        say(f"web panel ({who}): schedule saved — {len(saved['restarts'])} restart(s), "
            f"backups {'every ' + str(saved['backup_every_hours']) + ' h' if saved['backup_every_hours'] else 'off'}, "
            f"{len(saved['announcements'])} announcement(s)")
        return self.schedule_view()

    def _scheduled_backup(self) -> None:
        try:
            say("Scheduled backup")
            self.backup()
        finally:
            self._backup_running = False

    # ------------------------------------------------------------ web panel
    def _start_panel(self) -> None:
        """Up before the game boots, so a broken config can be fixed from the panel."""
        if "PANEL_PASSWORD" not in os.environ:
            say("Web panel: off — this server's egg has no Web Panel Password variable yet. Re-import "
                "egg/egg-dune-awakening.json (update the existing egg), set the password on the Startup "
                "tab, then press Restart.", "WARN")
            return
        if not self.sv["panel_password"]:
            say("Web panel: off (set Web Panel Password on the Startup tab, then press Restart)")
            return
        if len(self.sv["panel_password"]) < 10:
            say("Web panel: off — the Web Panel Password must be at least 10 characters", "WARN")
            return
        cfg, _ = configmod.load(self.base, self.scripts)
        if not self.sv["server_port"].isdigit():
            return
        port = int(self.sv["server_port"]) + cfg["ports"]["panel_offset"]
        try:
            self.panel = Panel(self, port, self.sv["panel_password"], LOG, say)
            self.panel.start()
        except OSError as exc:
            say(f"Web panel: could not listen on port {port}: {exc}", "WARN")
            return
        say(f"Web panel: http://<this server's IP>:{port} (port {port} must be allocated to this server)")

    def panel_status(self) -> dict:
        now = time.time()
        pending = None
        if self.pending:
            kind, at, reason = self.pending
            pending = {"kind": kind, "in": max(0, int(at - now)), "reason": reason}
        return {
            "version": __version__, "state": self.state, "server_name": self.sv["server_name"],
            "uptime": int(now - self.started_at) if self.state == "running" else 0,
            "joinable": bool(getattr(self, "joinable", False)),
            "players": self.players_online() if self.state == "running" else None,
            "services": {n: bool(p) for n, p in self.svc.status().items()},
            "pending": pending, "checks": getattr(self, "last_checks", []),
            "backups": self.list_backups()[:30], "ports": self.layout,
        }

    def panel_command(self, line: str) -> str | None:
        """Run a console line for the panel; output appears in the log. Returns an error or None."""
        cmd = console.parse(line, self.cfg["updates"]["warn_minutes"] * 60)
        if cmd.name == "error":
            return cmd.text
        if cmd.name == "unknown":
            return f"unknown command '{cmd.text}' — type help"
        if cmd.name == "stop":
            return "use Pterodactyl's Stop button — the panel stops with the server"
        threading.Thread(target=self._panel_run, args=(line,), daemon=True, name="panel-cmd").start()
        return None

    def _panel_run(self, line: str) -> None:
        try:
            self.handle(line)
        except Exception as exc:  # a command must never kill the panel
            say(f"command failed: {exc}", "ERROR")

    def run_doctor(self) -> list[dict]:
        # Only a server stopped waiting for a fix may take on new settings; a starting or
        # running one must keep the environment its services were started with.
        try:
            self.prepare(apply=self.state == "held")
            say("Health check finished.")
        except Held as exc:
            say(f"Health check: {exc}", "WARN")
        return getattr(self, "last_checks", [])

    def panel_info(self, what: str) -> dict:
        """Answers for the panel's Server information buttons (shown on the page, not the console)."""
        if what == "health":
            return {"checks": self.run_doctor()}
        if what == "ports":
            if not self.layout:
                return {"lines": ["No port layout yet (the primary allocation is missing)."], "problems": []}
            return {"lines": ports.describe(self.layout), "problems": ports.problems(self.layout), "layout": self.layout}
        if what == "config":
            _, problems = configmod.load(self.base, self.scripts, os.environ)
            return {"problems": problems}
        if what == "status":
            return self.panel_status()
        if what == "verbose":
            return {"verbose": bool(self.follower and self.follower.verbose)}
        raise ValueError("unknown information")

    def set_verbose(self, on: bool) -> dict:
        if self.follower:
            self.follower.verbose = on
        say("console shows all service output" if on else "console shows warnings and errors only")
        return {"verbose": on}

    def config_form(self) -> dict:
        return configform.describe(self.config_text(), self.scripts, os.environ)

    def save_config_form(self, changes, who: str = "") -> list[str]:
        """Apply form changes to the file's text, check it like a boot would, then save."""
        text = configform.set_values(self.config_text(), configform.coerce(changes, self.scripts))
        _, problems = configmod.parse_text(text, self.scripts)
        if problems:
            return problems
        configmod.save_text(self.base, text)
        n = sum(len(v) for v in (changes or {}).values())
        say(f"web panel ({who}): {n} setting(s) changed in config/dune.toml — `restart` to apply")
        return []

    def config_text(self) -> str:
        try:
            with open(configmod.config_path(self.base), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def save_config(self, text: str) -> list[str]:
        _, problems = configmod.parse_text(text, self.scripts)
        if problems:
            return problems
        configmod.save_text(self.base, text)
        say("config/dune.toml saved from the web panel — `restart` to apply")
        return []

    def _print_status(self) -> None:
        up = int(time.time() - self.started_at)
        say(f"Version {__version__} — state: {self.state}, up {up // 3600}h{(up % 3600) // 60:02d}m")
        for name, pid in self.svc.status().items():
            say(f"  {'✔' if pid else '✘'} {name}" + (f" (pid {pid})" if pid else ""))
        online = self.players_online() if self.state == "running" else None
        if online is not None:
            say(f"Players online: {online}")
        if self.pending:
            kind, at, reason = self.pending
            say(f"Pending: {kind} in {console.human(max(0, int(at - time.time())))}" + (f" — {reason}" if reason else ""))


def main(argv: list[str]) -> int:
    base = os.environ.get("DUNE_BASE_DIR", "/home/container")
    if len(argv) > 1 and argv[1] == "doctor":
        sup = Supervisor(base)
        try:
            sup.prepare()
            return 0
        except Held:
            return 1
    return Supervisor(base).run()
