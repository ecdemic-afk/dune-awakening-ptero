"""Follow every service log into the Pterodactyl console, readably.

The Director and the game servers write thousands of lines an hour; printing
them all buried the one line that mattered. Here the core services show
everything, the chatty ones show only warnings and errors, and a line repeated
over and over is collapsed to a count. Full logs stay in logs/.
"""
from __future__ import annotations

import os
import re
import threading

LEVEL_PATTERNS = [
    (re.compile(r"\[(DEBUG|INFO|WARN|ERROR)\]"), None),
    (re.compile(r"\[[0-9:]+ +[0-9]+ +(DBG|INF|WRN|ERR) "), {"DBG": "DEBUG", "INF": "INFO", "WRN": "WARN", "ERR": "ERROR"}),
    # Unreal: [2026.10.20-17.32.29:733][541][709][8]Category: Warning: ... (no verbosity = Log)
    (re.compile(r"^\[\d{4}\.\d\d\.\d\d-[\d.:]+\](?:\[ *\d*\])*\w+: (?:(Error|Warning|Fatal|Display|Verbose|VeryVerbose):)?"),
     {"Error": "ERROR", "Warning": "WARN", "Fatal": "ERROR", "Verbose": "DEBUG", "VeryVerbose": "DEBUG"}),
    # Unreal before its timestamps are on (early startup): "LogInit: Log: ...", "LogX: Display: ..."
    (re.compile(r"\b\w+: (Error|Warning|Fatal|Display|Log|Verbose|VeryVerbose):"),
     {"Error": "ERROR", "Warning": "WARN", "Fatal": "ERROR", "Verbose": "DEBUG", "VeryVerbose": "DEBUG"}),
    # ASP.NET console logger (text-router, gateway): "warn: Category[3]", message on the next line
    (re.compile(r"^(trce|dbug|info|warn|fail|crit): "),
     {"trce": "DEBUG", "dbug": "DEBUG", "info": "INFO", "warn": "WARN", "fail": "ERROR", "crit": "ERROR"}),
]
KEYWORDS_ERROR = re.compile(r"\b(error|fatal|failed|exception|critical|crash)\b", re.I)
KEYWORDS_WARN = re.compile(r"\b(warn|warning)\b", re.I)
RANK = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
# Known-harmless startup complaints (Funcom cloud services a self-hosted
# server does not use); shown only with `logs verbose`.
HARMLESS = re.compile(r"Ggwp config not available|GGWP is not configured|environment variable for IGWO"
                      r"|Setting '(GgwpApiKey|GgwpEndpointUrl|FlsGetServerFunctionsCode)' was not found"
                      # Funcom content errors every game server logs at load; nothing to act on
                      r"|LogMercuna: Error: Failed to merge nav grids|LogResourceField: Error: .* overlaps with another field"
                      r"|Ensure condition failed|Could not find template object for|HttpListener unable to bind"
                      # a game server shutting down still receives Director status messages
                      r"|TryParseDirectorState.*Couldn't find a valid world|failed to deserialize director state"
                      # pg_restore over partitioned tables (handled by the restore itself)
                      r"|cannot drop inherited constraint"
                      # Funcom's server list rejects the population report until the world's first
                      # heartbeat lands; the Director retries by itself
                      r"|Battlegroups_DeclarePopulationAndActivity|UnobservedTask exception!"
                      r"|LogResourceFieldSubsystem: Error: Couldn't find any fields"
                      r"|The WebRootPath was not found"
                      # Funcom content noise at map load and player login
                      r"|LogJson: Error: Json Value of type 'Null' used as a 'Object'"
                      r"|LogColdness: Error: .* not set!|LogCourier: Error: Failed to find valid PlayerController"
                      r"|GetMagnitude called for Data|Failed to remove last issued grant"
                      # the access-list reply is answered locally (fls-stub) and the game treats it as allowed
                      r"|Failed to parse PlayerAuthorizationsByResourceId"
                      # the Director polling the Kubernetes stand-in while it is down: one 25-line stack
                      # a second; the supervisor reports a stopped stand-in itself, in one line
                      r"|Http Request failed in ListKubernetesResourceJson|Failed to list IGWO server set scales"
                      r"|HttpsRedirectionMiddleware|determine the https port for redirect"
                      # Director housekeeping, and travel retries for map copies that are cold or still loading
                      r"|LOH compaction needed|Slow operation:|Failed to process travel queue for partition")
# Secrets the game prints in its own command line; never shown in the console
# (other Pterodactyl subusers can read it, and people paste it when asking for help).
SECRET = re.compile(r"((?:AuthToken|Password|ServiceAuthKey)=)[^\s\"']+|eyJ[\w-]{8,}\.[\w-]+\.[\w-]+")


def redact(text: str) -> str:
    return SECRET.sub(lambda m: (m.group(1) or "") + "***", text)


# Game servers' warnings are mostly content noise (missing packages, AI LOD);
# their errors still show.
LOUD_FLOOR = {"ue5": "ERROR"}
# Services whose INFO is noise in a console.
QUIET = re.compile(r"^(director|ue5-.*|mq-.*|postgres|text-router|gateway|mock-k8s)$")


def explicit_level(line: str) -> str | None:
    """The level the line states itself, or None when it doesn't say."""
    if HARMLESS.search(line):
        return "INFO"
    for rx, mapping in LEVEL_PATTERNS:
        m = rx.search(line)
        if m:
            return mapping.get(m.group(1), "INFO") if mapping else m.group(1)
    return None


def classify(line: str) -> str:
    level = explicit_level(line)
    if level:
        return level
    if KEYWORDS_ERROR.search(line):
        return "ERROR"
    if KEYWORDS_WARN.search(line):
        return "WARN"
    return "INFO"


def label(service: str) -> str:
    """ue5-Survival_1-p2 -> ue5:Survival_1 (the pool slot is noise to a reader)."""
    m = re.match(r"ue5-(.+?)(-dim\d+)?(-p\d+)?$", service)
    if m:
        return f"ue5:{m.group(1)}{m.group(2) or ''}"
    return service


class Follower(threading.Thread):
    def __init__(self, logs_dir: str, emit, verbose: bool = False, poll: float = 1.0):
        super().__init__(daemon=True, name="logstream")
        self.logs_dir, self.emit, self.verbose, self.poll = logs_dir, emit, verbose, poll
        self.offsets: dict[str, int] = {}
        self.last: dict[str, tuple[str, int]] = {}
        self.level: dict[str, str] = {}  # last stated level per service
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        # Start at the end of existing logs: history is in the files already.
        for path in self._paths():
            self.offsets[path] = os.path.getsize(path)
        while not self._halt.wait(self.poll):
            for path in self._paths():
                self._read(path)

    def _paths(self) -> list[str]:
        try:
            names = os.listdir(self.logs_dir)
        except OSError:
            return []
        return [os.path.join(self.logs_dir, n) for n in names if n.endswith(".log")]

    def _read(self, path: str) -> None:
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        pos = self.offsets.get(path, 0)
        if size < pos:
            # Services only ever append, so a shrink is rotate-logs trimming the file to its
            # last 50 MB: lines already shown. Re-reading from 0 replayed all of them, so carry
            # on from the new end once the rewrite has finished.
            size = self._settled_size(path, size)
            self.offsets[path] = size
            return
        if size == pos:
            return
        with open(path, "rb") as fh:
            fh.seek(pos)
            chunk = fh.read(min(size - pos, 1 << 20))
        consumed = chunk.rfind(b"\n") + 1
        if consumed == 0:
            return
        self.offsets[path] = pos + consumed
        service = os.path.basename(path)[:-4]
        for raw in chunk[:consumed].decode("utf-8", "replace").splitlines():
            self.line(service, raw.rstrip())

    def _settled_size(self, path: str, size: int, wait: float = 0.25, limit: int = 40) -> int:
        """The file's size once it stops changing (the trim rewrites it in place)."""
        for _ in range(limit):
            if self._halt.wait(wait):
                break
            try:
                now = os.path.getsize(path)
            except OSError:
                return size
            if now == size:
                break
            size = now
        return size

    def line(self, service: str, text: str) -> None:
        if not text:
            return
        text = redact(text)
        level = explicit_level(text)
        if level:
            self.level[service] = level
        else:
            # A service that states levels writes multi-line messages: an unmarked
            # line ("Error:Partition's ServerId is null") belongs to the line above.
            level = self.level.get(service) or classify(text)
        floor = "INFO" if self.verbose or not QUIET.match(service) else LOUD_FLOOR.get(service.split("-")[0], "WARN")
        if RANK[level] < RANK[floor]:
            return
        # Collapse repeats: show the first, count the rest, report on change.
        prev = self.last.get(service)
        if prev and prev[0] == text:
            self.last[service] = (text, prev[1] + 1)
            return
        if prev and prev[1] > 0:
            self.emit(label(service), f"(previous line repeated {prev[1]} more times)", "INFO")
        self.last[service] = (text, 0)
        self.emit(label(service), text, level)
