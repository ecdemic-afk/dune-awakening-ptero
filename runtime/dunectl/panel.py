"""Web panel: a password-protected page served by the supervisor itself.

Everything it does goes through the same paths as the console (commands are
console lines), so the panel and the console can never disagree. Stdlib only:
the page is static files in web/, the API is JSON.

Security model: one shared password (Startup tab). Plain HTTP, so it is meant
for the LAN or behind a reverse proxy with HTTPS. Sessions are random tokens in
an HttpOnly, SameSite=Strict cookie; every state change also needs the X-Dune
header, which a cross-site form cannot send. Repeated wrong passwords lock that
address out for a minute.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, unquote_plus

from .players import FLS_RE

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
STATIC = {"/": ("index.html", "text/html; charset=utf-8")}
# Every other static file: a flat name (or maps/<name>) with a known type — no path tricks possible.
STATIC_RE = re.compile(r"^/((?:maps/(?:icons/)?)?[a-z0-9-]+\.(js|css|webp|svg|png))$")
TYPES = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "webp": "image/webp",
         "svg": "image/svg+xml", "png": "image/png"}
SESSION_SECS = 12 * 3600
MAX_FAILS, LOCK_SECS = 5, 60
MAX_BODY = 1 << 20
COOKIE = "dune_session"


class Auth:
    """Password check, sessions and lockout — no I/O, so it is testable."""

    def __init__(self, password: str, now=time.time):
        self.password, self.now = password, now
        self.sessions: dict[str, float] = {}
        self.fails: dict[str, tuple[int, float]] = {}
        self.lock = threading.Lock()

    def locked(self, ip: str) -> int:
        n, since = self.fails.get(ip, (0, 0.0))
        left = int(since + LOCK_SECS - self.now())
        return left if n >= MAX_FAILS and left > 0 else 0

    def login(self, ip: str, password: str) -> str | None:
        with self.lock:
            if self.locked(ip):
                return None
            if hmac.compare_digest(password.encode(), self.password.encode()):
                self.fails.pop(ip, None)
                token = secrets.token_urlsafe(32)
                self.sessions[token] = self.now() + SESSION_SECS
                return token
            n, _ = self.fails.get(ip, (0, 0.0))
            self.fails[ip] = (0 if n >= MAX_FAILS else n) + 1, self.now()
            return None

    def valid(self, token: str | None) -> bool:
        with self.lock:
            exp = self.sessions.get(token or "")
            if exp is None:
                return False
            if exp < self.now():
                del self.sessions[token]
                return False
            return True

    def logout(self, token: str | None) -> None:
        with self.lock:
            self.sessions.pop(token or "", None)


class Panel:
    def __init__(self, app, port: int, password: str, log, say):
        self.app, self.port, self.log, self.say = app, port, log, say
        self.auth = Auth(password)
        self.httpd: ThreadingHTTPServer | None = None

    def start(self) -> None:
        panel = self

        class Handler(_Handler):
            pass
        Handler.panel = panel
        self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="panel").start()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


class _Handler(BaseHTTPRequestHandler):
    panel: Panel
    server_version = "dune-panel"
    sys_version = ""

    def log_message(self, *args) -> None:  # requests are not console news
        pass

    # ---- plumbing -----------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if "Cache-Control" not in (extra or {}):
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj, extra: dict | None = None) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json", extra)

    def _token(self) -> str | None:
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def _body(self) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n > MAX_BODY:
            return None
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _authed(self) -> bool:
        if self.panel.auth.valid(self._token()):
            return True
        self._json(401, {"error": "sign in"})
        return False

    # ---- routes -------------------------------------------------------
    def _get(self) -> None:
        path, _, query = self.path.partition("?")
        m = STATIC_RE.match(path)
        if path in STATIC or m:
            name, ctype = STATIC[path] if path in STATIC else (m.group(1), TYPES[m.group(2)])
            try:
                with open(os.path.join(WEB, name), "rb") as fh:
                    return self._send(200, fh.read(), ctype)
            except OSError:
                return self._send(404, b"missing", "text/plain")
        if not path.startswith("/api/"):
            return self._send(404, b"not found", "text/plain")
        if not self._authed():
            return
        app = self.panel.app
        if path == "/api/status":
            return self._json(200, app.panel_status())
        if path == "/api/log":
            after = 0
            for part in query.split("&"):
                k, _, v = part.partition("=")
                if k == "after" and v.isdigit():
                    after = int(v)
            rows = [{"seq": s, "t": t, "tag": tag, "level": lv, "line": line}
                    for s, t, tag, lv, line in list(self.panel.log) if s > after]
            return self._json(200, {"lines": rows[-1000:]})
        if path == "/api/config":
            return self._json(200, {"text": app.config_text()})
        if path == "/api/config/form":
            return self._json(200, app.config_form())
        if path.startswith("/api/info/"):
            try:
                return self._json(200, app.panel_info(path[len("/api/info/"):]))
            except ValueError as exc:
                return self._json(404, {"error": str(exc)})
        tools = getattr(app, "players", None)
        if tools is not None:
            q = dict(p.partition("=")[::2] for p in query.split("&") if p)
            try:
                if path == "/api/players":
                    return self._json(200, {"players": tools.list()})
                if path.startswith("/api/players/"):
                    fls = unquote(path[len("/api/players/"):])
                    if not FLS_RE.match(fls):
                        return self._json(400, {"error": "bad player id"})
                    return self._json(200, tools.detail(fls.upper()))
                if path == "/api/catalog":
                    off = q.get("offset", "0")
                    return self._json(200, tools.browse(unquote_plus(q.get("q", "")), unquote_plus(q.get("cat", "")),
                                                        int(off) if off.isdigit() else 0))
                if path.startswith("/api/item-image/"):
                    img = app.images.lookup(path[len("/api/item-image/"):])
                    if img == "pending":
                        return self._send(503, b"", "image/png", {"Retry-After": "3"})
                    if not img:
                        return self._send(404, b"", "image/png", {"Cache-Control": "private, max-age=3600"})
                    return self._send(200, img[0], img[1], {"Cache-Control": "private, max-age=604800"})
                if path == "/api/instances":
                    return self._json(200, app.instances_view())
                if path == "/api/vehicles":
                    return self._json(200, {"vehicles": app.world.vehicles()})
                if path == "/api/locations":
                    return self._json(200, {"locations": app.world.locations()})
                if path == "/api/items":
                    return self._json(200, {"items": tools.search_items(unquote_plus(q.get("q", "")))})
                if path == "/api/kits":
                    names = tools.item_names()
                    return self._json(200, {"kits": [dict(k, items=[dict(i, name=names.get(i["id"], i["id"]))
                                                                     for i in k["items"]]) for k in tools.kits()]})
                if path == "/api/bans":
                    return self._json(200, {"bans": tools.bans()})
                world = app.world
                if path == "/api/maps":
                    return self._json(200, {"maps": world.maps()})
                if path.startswith("/api/map/") and path.endswith("/resources"):
                    return self._json(200, world.resources(path.split("/")[3]))
                if path.startswith("/api/map/"):
                    return self._json(200, {"markers": world.markers(path[len("/api/map/"):])})
                if path == "/api/welcome":
                    return self._json(200, tools.welcome())
                if path == "/api/bases":
                    return self._json(200, {"bases": world.bases(), "queued": world.queued()})
                if path.startswith("/api/bases/"):
                    return self._json(200, world.base_detail(path[len("/api/bases/"):]))
                if path == "/api/guilds":
                    return self._json(200, {"guilds": world.guilds()})
                if path.startswith("/api/guilds/"):
                    return self._json(200, world.guild(path[len("/api/guilds/"):]))
                if path == "/api/ops":
                    return self._json(200, {"ops": world.ops()})
                if path == "/api/schedule":
                    return self._json(200, app.schedule_view())
            except LookupError as exc:
                return self._json(404, {"error": str(exc)})
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                return self._json(503, {"error": str(exc)})
        return self._json(404, {"error": "not found"})

    def _post(self) -> None:
        path = self.path.partition("?")[0]
        if self.headers.get("X-Dune") != "1":  # a cross-site form cannot set this header
            return self._json(403, {"error": "missing X-Dune header"})
        body = self._body()
        if body is None:
            return self._json(400, {"error": "bad request"})
        auth = self.panel.auth
        ip = self.client_address[0]
        if path == "/api/login":
            wait = auth.locked(ip)
            if wait:
                return self._json(429, {"error": f"too many wrong passwords — try again in {wait}s"})
            token = auth.login(ip, str(body.get("password", "")))
            if not token:
                self.panel.say(f"web panel: wrong password from {ip}", "WARN")
                time.sleep(1)
                return self._json(401, {"error": "wrong password"})
            self.panel.say(f"web panel: signed in from {ip}")
            return self._json(200, {"ok": True}, {
                "Set-Cookie": f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECS}"})
        if path == "/api/logout":
            auth.logout(self._token())
            return self._json(200, {"ok": True}, {"Set-Cookie": f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
        if not self._authed():
            return
        app = self.panel.app
        if path == "/api/command":
            line = str(body.get("line", "")).strip()
            if not line:
                return self._json(400, {"error": "empty command"})
            self.panel.say(f"web panel ({ip}): {line}")
            err = app.panel_command(line)
            return self._json(400 if err else 200, {"error": err} if err else {"ok": True})
        if path == "/api/config/form":
            try:
                problems = app.save_config_form(body.get("changes"), ip)
            except ValueError as exc:
                problems = [str(exc)]
            return self._json(400 if problems else 200, {"problems": problems})
        if path == "/api/verbose":
            return self._json(200, app.set_verbose(bool(body.get("on"))))
        if path == "/api/config":
            problems = app.save_config(str(body.get("text", "")))
            return self._json(400 if problems else 200, {"problems": problems})
        tools = getattr(app, "players", None)
        if tools is not None:
            try:
                if path == "/api/kits":
                    return self._json(200, {"kits": tools.save_kits(body.get("kits"))})
                if path == "/api/gift":
                    return self._json(200, tools.gift(body, ip))
                if path == "/api/welcome":
                    return self._json(200, tools.save_welcome(body, ip))
                if path.startswith("/api/players/") and path.count("/") == 4:
                    fls, action = path[len("/api/players/"):].split("/")
                    if action == "goto":
                        return self._json(200, app.world.teleport_to(unquote(fls), str(body.get("location", "")), ip))
                    return self._json(200, tools.act(unquote(fls), action, body, ip))
                if path == "/api/instances":
                    part = body.get("partition")
                    if not isinstance(part, int) or isinstance(part, bool):
                        raise ValueError("partition must be a number")
                    return self._json(200, app.instance_ctl.act(part, str(body.get("action", "")),
                                                               body.get("force") is True, ip))
                if path == "/api/vehicles/spawn":
                    return self._json(200, app.world.spawn_vehicle(str(body.get("player", "")), str(body.get("vehicle", "")),
                                                                   str(body.get("variant", "")), ip))
                if path == "/api/locations":
                    return self._json(200, {"locations": app.world.save_location(body, ip)})
                if path == "/api/locations/delete":
                    return self._json(200, {"locations": app.world.delete_location(str(body.get("name", "")), ip)})
                world = app.world
                if path.startswith("/api/bases/") and path.endswith("/refill"):
                    return self._json(200, world.refill(path.split("/")[3], str(body.get("kind", "")), ip))
                if path == "/api/guilds":
                    return self._json(200, world.guild_create(body, ip))
                if path.startswith("/api/guilds/") and path.count("/") == 4:
                    gid, action = path[len("/api/guilds/"):].split("/")
                    return self._json(200, world.guild_act(gid, action, body, ip))
                if path == "/api/ops":
                    return self._json(200, world.run(str(body.get("op", "")), body.get("args") or [], ip))
                if path == "/api/schedule":
                    return self._json(200, app.save_schedule(body.get("schedule"), ip))
            except (ValueError, LookupError) as exc:
                return self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                return self._json(503, {"error": str(exc)})
        return self._json(404, {"error": "not found"})

    # A bug in one page must answer with an error, not drop the connection.
    def do_GET(self) -> None:
        self._guarded(self._get)

    def do_POST(self) -> None:
        self._guarded(self._post)

    def _guarded(self, fn) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — reported to the operator below
            self.panel.say(f"web panel: {self.command} {self.path.partition('?')[0]} failed: {exc!r}", "ERROR")
            try:
                self._json(500, {"error": f"the panel hit an error: {exc}"})
            except OSError:
                pass
