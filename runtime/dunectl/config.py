"""config/dune.toml: seed, load, validate, and turn into the core's environment.

One file replaces ~60 Pterodactyl startup variables. The template next to this
module is both the file seeded on first boot and the source of every default,
so a key cannot exist in one place and not the other.

Validation is strict on purpose: an unknown key is almost always a typo, and a
typo that silently does nothing is the failure this project exists to remove.
Every problem names the line it came from.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "dune.toml.template")
CONFIG_REL = os.path.join("config", "dune.toml")

APP_IDS = {"live": "4754530", "ptc": "3104830"}

# Gameplay setting ids the config owns elsewhere — setting them in [gameplay]
# would fight the other source, so they are refused with a pointer.
OWNED_ELSEWHERE = {
    "server_password": "the Startup tab's Server Password",
    "server_display_name": "[world] display_name",
    "max_concurrent_instances": "[instances] max_concurrent",
    "auto_stop_duration": "[instances] auto_stop",
    "always_warm_maps": "[instances] always_warm",
    "pvp_enabled_partitions": "[gameplay] pvp_partitions",
}
# [gameplay] keys that are not schema settings but handled specially.
GAMEPLAY_SPECIAL = {"pvp_partitions", "deep_desert_picker_routing"}

# Numeric bounds: (section, key) -> (min, max)
BOUNDS = {
    ("ports", "block_size"): (8, 200),
    ("ports", "game_slots"): (3, 64),
    ("ports", "dimension_slots"): (0, 64),
    ("ports", "mq_game_offset"): (0, 199),
    ("ports", "mq_mgmt_offset"): (0, 199),
    ("ports", "panel_offset"): (0, 199),
    ("instances", "max_concurrent"): (1, 64),
    ("instances", "deep_desert_dimensions"): (0, 12),
    ("instances", "arrakeen_dimensions"): (0, 12),
    ("instances", "harko_dimensions"): (0, 12),
    ("instances", "fallen_light_dimensions"): (0, 12),
    ("instances", "extra_sietches"): (0, 20),
    ("updates", "warn_minutes"): (0, 120),
    ("backups", "keep"): (1, 365),
    ("advanced", "game_down_grace"): (15, 3600),
    ("advanced", "core_down_grace"): (5, 3600),
    ("advanced", "max_auto_restarts"): (0, 20),
}
DURATION_RE = re.compile(r"^\d+[smh]$")
MAP_RE = re.compile(r"^[A-Za-z0-9_]+$")
# Startup tab variables that, when not empty, override [instances] in dune.toml
# (so which maps run can be changed from the panel without the File Manager).
STARTUP_INSTANCES = {
    "WARM_MAPS": "always_warm",
    "MAX_RUNNING_MAPS": "max_concurrent",
    "IDLE_MAP_STOP": "auto_stop",
    "DEEP_DESERT_COPIES": "deep_desert_dimensions",
    "ARRAKEEN_COPIES": "arrakeen_dimensions",
    "HARKO_COPIES": "harko_dimensions",
}
DIRECTOR_LEVELS = ("Verbose", "Debug", "Information", "Warning", "Error")


def defaults() -> dict:
    with open(TEMPLATE, "rb") as fh:
        return tomllib.load(fh)


def config_path(base: str) -> str:
    return os.path.join(base, CONFIG_REL)


def seed(base: str) -> bool:
    """Write the commented template on first boot. Never overwrites."""
    path = config_path(base)
    if os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shutil.copyfile(TEMPLATE, path)
    return True


def load_schema(scripts_dir: str) -> dict:
    """settings-schema.json by id. Lives in the core's data/admin."""
    path = os.path.join(os.path.dirname(scripts_dir), "data", "admin", "settings-schema.json")
    with open(path, encoding="utf-8") as fh:
        return {s["id"]: s for s in json.load(fh)["settings"]}


def _line_of(text: str, section: str, key: str) -> int | None:
    """Best-effort line number of `key` inside `[section]` (1-based)."""
    current = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
        elif current == section and re.match(rf"{re.escape(key)}\s*=", line):
            return n
    return None


def _hint(name: str, choices) -> str:
    near = difflib.get_close_matches(name, list(choices), n=1, cutoff=0.6)
    return f" — did you mean `{near[0]}`?" if near else ""


def _type_ok(default, value) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, list):
        return isinstance(value, list)
    return isinstance(value, type(default))


def _type_name(default) -> str:
    return {bool: "true/false", int: "a whole number", float: "a number",
            str: "text in quotes", list: "a list like [1, 2]"}.get(type(default), "a value")


def validate(user: dict, text: str, schema: dict, ini_normalize) -> tuple[dict, list[str]]:
    """Merge `user` over the defaults. Returns (config, problems)."""
    base = defaults()
    problems: list[str] = []

    def where(section, key):
        n = _line_of(text, section, key)
        return f"line {n}: " if n else ""

    for section, values in user.items():
        if not isinstance(values, dict):
            problems.append(f"`{section}` must be a [section]{_hint(section, base)}")
            continue
        if section not in base:
            problems.append(f"unknown section [{section}]{_hint(section, base)}")
            continue
        for key, value in values.items():
            if section == "gameplay" and key not in GAMEPLAY_SPECIAL:
                if key in OWNED_ELSEWHERE:
                    problems.append(f"{where(section, key)}`{key}` is set in {OWNED_ELSEWHERE[key]}, not here")
                    continue
                st = schema.get(key)
                if st is None:
                    problems.append(f"{where(section, key)}unknown setting `{key}`{_hint(key, schema)}")
                    continue
                raw = value
                if isinstance(value, bool):
                    raw = "true" if value else "false"
                try:
                    ini_normalize(raw, st.get("type", "string"), st.get("enum"))
                except (ValueError, TypeError) as exc:
                    problems.append(f"{where(section, key)}`{key}`: {exc}")
                    continue
                base[section][key] = value
                continue
            if key not in base[section]:
                problems.append(f"{where(section, key)}unknown key `{key}` in [{section}]{_hint(key, base[section])}")
                continue
            default = base[section][key]
            if not _type_ok(default, value):
                problems.append(f"{where(section, key)}`{key}` must be {_type_name(default)}")
                continue
            lo_hi = BOUNDS.get((section, key))
            if lo_hi and not (lo_hi[0] <= value <= lo_hi[1]):
                problems.append(f"{where(section, key)}`{key}` must be between {lo_hi[0]} and {lo_hi[1]}")
                continue
            base[section][key] = value

    inst, ports = base["instances"], base["ports"]
    if not DURATION_RE.match(inst["auto_stop"]):
        problems.append(f"{where('instances', 'auto_stop')}`auto_stop` must look like 10m, 90s or 1h")
    if base["advanced"]["director_log_level"] not in DIRECTOR_LEVELS:
        problems.append(f"{where('advanced', 'director_log_level')}`director_log_level` must be one of {', '.join(DIRECTOR_LEVELS)}")
    for p in base["gameplay"]["pvp_partitions"]:
        if not isinstance(p, int) or isinstance(p, bool) or p < 1:
            problems.append(f"{where('gameplay', 'pvp_partitions')}`pvp_partitions` must hold partition numbers, e.g. [8, 101]")
            break
    if not isinstance(base["gameplay"]["deep_desert_picker_routing"], bool):
        problems.append(f"{where('gameplay', 'deep_desert_picker_routing')}`deep_desert_picker_routing` must be true/false")
    dims = dimension_count(base)
    if dims > ports["dimension_slots"]:
        problems.append(f"{dims} dimensions/sietches are configured but [ports] dimension_slots is "
                        f"{ports['dimension_slots']} — raise dimension_slots (and allocate the ports)")
    # Port offsets: inside the block, not inside the UDP ranges, all different.
    udp_end = ports["game_slots"] + ports["dimension_slots"]
    tcp = {k: ports[k] for k in ("mq_game_offset", "mq_mgmt_offset", "panel_offset")}
    if udp_end > ports["block_size"]:
        problems.append(f"game_slots + dimension_slots ({udp_end}) exceeds block_size ({ports['block_size']})")
    for k, off in tcp.items():
        if off >= ports["block_size"]:
            problems.append(f"{where('ports', k)}`{k}` ({off}) is outside the block (block_size {ports['block_size']})")
        elif off < udp_end:
            problems.append(f"{where('ports', k)}`{k}` ({off}) overlaps the game ports (offsets 0-{udp_end - 1})")
    if len(set(tcp.values())) != len(tcp):
        problems.append("mq_game_offset, mq_mgmt_offset and panel_offset must all differ")
    return base, problems


def apply_startup(cfg: dict, environ) -> tuple[list[str], list[str]]:
    """Override [instances] with the Startup tab's values. Returns (problems, applied)."""
    inst, problems, applied = cfg["instances"], [], []
    for env, key in STARTUP_INSTANCES.items():
        raw = (environ.get(env) or "").strip()
        if not raw:
            continue
        where = f"Startup tab {env}"
        if key == "always_warm":
            maps = [] if raw.lower() == "none" else [m for m in re.split(r"[,\s]+", raw) if m]
            bad = [m for m in maps if not MAP_RE.match(m)]
            if bad:
                problems.append(f"{where}: `{bad[0]}` is not a map name — use names like Survival_1,Overmap")
                continue
            value = maps
        elif key == "auto_stop":
            if not DURATION_RE.match(raw):
                problems.append(f"{where} must look like 10m, 90s or 1h")
                continue
            value = raw
        else:
            lo, hi = BOUNDS[("instances", key)]
            if not raw.isdigit() or not lo <= int(raw) <= hi:
                problems.append(f"{where} must be a whole number between {lo} and {hi}")
                continue
            value = int(raw)
        inst[key] = value
        applied.append(f"{key} = {', '.join(value) or 'none' if isinstance(value, list) else value}")
    dims, slots = dimension_count(cfg), cfg["ports"]["dimension_slots"]
    if applied and dims > slots:
        problems.append(f"the Startup tab asks for {dims} map copies but [ports] dimension_slots is {slots} "
                        "— lower the copies or raise dimension_slots (and allocate the ports)")
    return problems, applied


def dimension_count(cfg: dict) -> int:
    i = cfg["instances"]
    return (i["deep_desert_dimensions"] + i["arrakeen_dimensions"] + i["harko_dimensions"]
            + (i["fallen_light_dimensions"] if i["fallen_light"] else 0) + i["extra_sietches"])


def load(base: str, scripts_dir: str, environ=None) -> tuple[dict, list[str]]:
    """(config, problems). A syntax error returns the defaults plus the error.
    With `environ`, the Startup tab's instance variables override the file."""
    cfg, problems = _load_file(base, scripts_dir)
    if environ is not None:
        extra, applied = apply_startup(cfg, environ)
        problems += extra
        cfg["_startup_applied"] = applied
    return cfg, problems


def _load_file(base: str, scripts_dir: str) -> tuple[dict, list[str]]:
    try:
        with open(config_path(base), "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return defaults(), []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return defaults(), [f"{CONFIG_REL} is not UTF-8 text"]
    return parse_text(text, scripts_dir)


def parse_text(text: str, scripts_dir: str) -> tuple[dict, list[str]]:
    sys.path.insert(0, scripts_dir)
    import admin_ini_merge  # core: the tested INI value normalizer
    try:
        user = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return defaults(), [f"{CONFIG_REL} is not valid TOML: {exc}"]
    return validate(user, text, load_schema(scripts_dir), admin_ini_merge.normalize_value)


def save_text(base: str, text: str) -> None:
    """Atomic write, so the server never reads a half-written file."""
    path = config_path(base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def startup_vars(environ) -> dict:
    """The few values Pterodactyl's Startup tab owns, cleaned."""
    g = lambda k, d="": (environ.get(k) or d).strip()
    branch = g("BRANCH", "live").lower()
    return {
        "token": g("DUNE_TOKEN"),
        "server_name": g("SERVER_NAME", "Dune Server"),
        "sietch_name": g("SIETCH_NAME"),
        "panel_password": environ.get("PANEL_PASSWORD", ""),
        "password": environ.get("SERVER_PASSWORD", ""),
        "region": g("REGION", "Europe"),
        "external_ip": g("EXTERNAL_IP", "auto"),
        "branch": branch if branch in APP_IDS else "live",
        "auto_update": g("AUTO_UPDATE"),
        "discord_webhook": g("DISCORD_WEBHOOK"),
        "server_port": g("SERVER_PORT"),
    }


def core_env(cfg: dict, sv: dict, external_ip: str, layout: dict) -> dict:
    """Environment the reused core scripts read (they were written for egg variables)."""
    inst, world = cfg["instances"], cfg["world"]
    env = {
        "DUNE_JWT": sv["token"],
        "DUNE_WORLD_TITLE": sv["server_name"],
        "DUNE_REGION": sv["region"],
        "DUNE_EXTERNAL_IP": external_ip,
        "DUNE_BIND_IP": "0.0.0.0",
        "DUNE_HOST_DC_ID": world["datacenter_id"],
        "SRCDS_APPID": APP_IDS[sv["branch"]],
        "DUNE_RELEASE_VERSION": APP_IDS[sv["branch"]],
        "K8S_POOL_SIZE": str(layout["game_slots"]),
        "K8S_POOL_GAME_PORT_BASE": str(layout["game_base"]),
        "DUNE_DIM_GAME_PORT_BASE": str(layout["dim_base"]),
        "DUNE_MQ_GAME_PORT": str(layout["mq_game"]),
        "DUNE_MQ_GAME_MGMT_PORT": str(layout["mq_mgmt"]),
        "DUNE_DD_DIMENSIONS": str(inst["deep_desert_dimensions"]),
        "DUNE_ARRAKEEN_DIMENSIONS": str(inst["arrakeen_dimensions"]),
        "DUNE_HARKO_DIMENSIONS": str(inst["harko_dimensions"]),
        "DUNE_ENABLE_FALLEN_LIGHT": "true" if inst["fallen_light"] else "false",
        "DUNE_FALLEN_LIGHT_DIMENSIONS": str(inst["fallen_light_dimensions"]),
        "DUNE_SURVIVAL_SIETCHES": str(inst["extra_sietches"]),
        "DUNE_DIRECTOR_LOG_LEVEL": cfg["advanced"]["director_log_level"],
        # The old egg's admin UI is not part of this project.
        "DUNE_ADMIN_UI_ENABLED": "0",
    }
    if world["world_name_override"]:
        env["DUNE_WORLD_NAME_OVERRIDE"] = world["world_name_override"]
    return env
