"""The web panel's settings editor: config/dune.toml as forms.

describe() turns the file into fields (type, limits, help from the template's
comments, current value); set_values() writes changes back into the existing
text — only the changed lines move, so the operator's comments and the plain
text view stay intact. The result is checked with the same validation as a
boot before anything is saved.
"""
from __future__ import annotations

import json
import re
import tomllib

from . import config

SECTION_TITLES = {
    "world": "World", "ports": "Ports", "instances": "Maps & instances", "gameplay": "Gameplay",
    "updates": "Updates", "backups": "Backups", "discord": "Discord alerts", "advanced": "Advanced",
}
LABELS = {
    "display_name": "Sietch name", "world_name_override": "World name (moving a world here)",
    "datacenter_id": "Datacenter label", "block_size": "Port block size", "game_slots": "Game ports",
    "dimension_slots": "Map-copy ports", "mq_game_offset": "Game broker port (offset)",
    "mq_mgmt_offset": "Broker management port (offset)", "panel_offset": "Web panel port (offset)",
    "max_concurrent": "Max running maps", "auto_stop": "Stop empty maps after",
    "always_warm": "Always-running maps", "deep_desert_dimensions": "Deep Desert copies",
    "arrakeen_dimensions": "Arrakeen copies", "harko_dimensions": "Harko Village copies",
    "fallen_light": "Fallen Light map", "fallen_light_dimensions": "Fallen Light copies",
    "extra_sietches": "Extra sietches", "pvp_partitions": "PvP partitions",
    "deep_desert_picker_routing": "Deep Desert picker routing", "warn_minutes": "Countdown (minutes)",
    "check_on_start": "Check for game updates at start", "keep": "Backups to keep",
    "director_log_level": "Director log level", "game_down_grace": "Game servers down grace (s)",
    "core_down_grace": "Core service down grace (s)", "max_auto_restarts": "Automatic restarts per 30 min",
}
CHOICES = {("advanced", "director_log_level"): list(config.DIRECTOR_LEVELS)}
STARTUP_VAR = {v: k for k, v in config.STARTUP_INSTANCES.items()}
GAMEPLAY_TYPES = {"bool": "bool", "cvarbool": "bool", "int": "int", "float": "float",
                  "string": "text", "struct": "text", "array": "text"}


def _label(key: str) -> str:
    return LABELS.get(key) or key.replace("_", " ").capitalize()


def template_help() -> dict:
    """(section, key) -> the comment lines above it in the template (plus a trailing comment);
    (section, "") -> the section's introduction (comments right under the header, then a blank line)."""
    out, section, block = {}, "", []
    with open(config.TEMPLATE, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            m = re.match(r"^\[(\w+)\]$", line)
            if m:
                section, block = m.group(1), []
            elif (not line and section and block and (section, "") not in out
                  and not any(s == section for s, k in out)):
                out[(section, "")] = " ".join(block)  # intro: before the section's first setting
                block = []
            elif line.startswith("#"):
                text = line.lstrip("#").strip()
                if text and not set(text) <= set("=-"):
                    block.append(text)
            elif "=" in line and section:
                key, _, rest = line.partition("=")
                inline = rest.split(" # ", 1)[1].strip() if " # " in rest else ""
                out[(section, key.strip())] = " ".join(block + ([inline] if inline else []))
                block = []
            else:
                block = []
    return out


def _field(section: str, key: str, value, default, help_text: str, environ) -> dict:
    kind = ("bool" if isinstance(default, bool) else "int" if isinstance(default, int)
            else "float" if isinstance(default, float) else "list" if isinstance(default, list) else "text")
    f = {"section": section, "key": key, "label": _label(key), "help": help_text, "type": kind,
         "value": value, "default": default}
    if kind == "list":
        f["item"] = "int" if key == "pvp_partitions" else "text"
    if (section, key) in config.BOUNDS:
        f["min"], f["max"] = config.BOUNDS[(section, key)]
    if (section, key) in CHOICES:
        f["type"], f["options"] = "choice", CHOICES[(section, key)]
    if key == "auto_stop":
        f["type"], f["pattern"] = "duration", r"\d+[smh]"
    var = STARTUP_VAR.get(key) if section == "instances" else None
    if var:
        f["startup_var"] = var
        f["startup_value"] = (environ.get(var) or "").strip()
    return f


def describe(text: str, scripts_dir: str, environ) -> dict:
    cfg, problems = config.parse_text(text, scripts_dir)
    try:
        user = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        user = {}
    base, helps = config.defaults(), template_help()
    sections = []
    for sec, keys in base.items():
        fields = [_field(sec, k, cfg[sec].get(k, d), d, helps.get((sec, k), ""), environ)
                  for k, d in keys.items()]
        sections.append({"id": sec, "title": SECTION_TITLES.get(sec, sec.capitalize()), "fields": fields,
                         "intro": helps.get((sec, ""), "")})
    schema = config.load_schema(scripts_dir)
    set_here = user.get("gameplay", {}) if isinstance(user.get("gameplay"), dict) else {}
    settings = []
    for sid, st in schema.items():
        if sid in config.OWNED_ELSEWHERE or st.get("file") == "ondemand" or st.get("repeated"):
            continue
        kind = GAMEPLAY_TYPES.get(st.get("type"), "text")
        if st.get("enum"):
            kind = "choice"
        settings.append({
            "id": sid, "label": st.get("label") or sid, "category": st.get("category") or "Other",
            "type": kind, "options": st.get("enum") or [], "default": st.get("default", ""),
            "value": set_here.get(sid), "set": sid in set_here, "range": st.get("range") or "",
            "advanced": bool(st.get("advanced")), "client": st.get("client_gated") == "yes",
        })
    return {"sections": sections, "gameplay": settings, "problems": problems}


# ---- writing ---------------------------------------------------------------
def render(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string
    if isinstance(value, list):
        return "[" + ", ".join(render(v) for v in value) + "]"
    raise ValueError(f"unsupported value {value!r}")


HEADER = re.compile(r"^\s*\[\s*([A-Za-z0-9_-]+)\s*\]\s*(#.*)?$")


def _bounds(lines: list[str], section: str) -> tuple[int, int] | None:
    start = None
    for i, line in enumerate(lines):
        m = HEADER.match(line)
        if m and start is None and m.group(1) == section:
            start = i + 1
        elif m and start is not None:
            return start, i
    return (start, len(lines)) if start is not None else None


def _value_span(lines: list[str], i: int) -> int:
    """Lines a value occupies (a multi-line array runs until its brackets close)."""
    depth, n = 0, 0
    for line in lines[i:]:
        n += 1
        code = line.split("#", 1)[0]
        depth += code.count("[") - code.count("]")
        if depth <= 0:
            return n
    return n


def _trailing_comment(line: str, key: str) -> str:
    """A ' # comment' after a single-line value, kept when the value changes."""
    _, _, rest = line.partition("=")
    rest = rest.rstrip("\r\n")
    for i in [m.start() for m in re.finditer(r"#", rest)]:
        cut = len(rest[:i].rstrip())  # keep the original spacing before the comment
        if cut == len(rest[:i]):
            continue  # '#' glued to the value: part of it, not a comment
        try:
            tomllib.loads(f"{key} = {rest[:cut]}")
            return rest[cut:]
        except tomllib.TOMLDecodeError:
            continue
    return ""


def set_values(text: str, changes: dict) -> str:
    """changes: {section: {key: new value, or None to remove the line (back to the default)}}."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += nl
    for section, kv in changes.items():
        if not re.match(r"^[a-z_]+$", section) or not isinstance(kv, dict):
            raise ValueError(f"bad section {section!r}")
        for key, value in kv.items():
            if not re.match(r"^[A-Za-z0-9_]+$", key):
                raise ValueError(f"bad key {key!r}")
            span = _bounds(lines, section)
            if span is None:
                if value is None:
                    continue
                lines += [nl, f"[{section}]{nl}"]
                span = (len(lines), len(lines))
            start, end = span
            key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
            at = next((i for i in range(start, end) if key_re.match(lines[i])), None)
            if value is None:
                if at is not None:
                    del lines[at:at + _value_span(lines, at)]
                continue
            if at is not None:
                n = _value_span(lines, at)
                comment = _trailing_comment(lines[at], key) if n == 1 else ""
                lines[at:at + n] = [f"{key} = {render(value)}{comment}{nl}"]
            else:
                pos = end
                while pos > start and not lines[pos - 1].strip():
                    pos -= 1  # keep the blank line before the next section
                lines.insert(pos, f"{key} = {render(value)}{nl}")
    return "".join(lines)


def coerce(changes: dict, scripts_dir: str) -> dict:
    """JSON numbers -> the setting's type, so 2 for a float setting is written as 2.0."""
    base, schema = config.defaults(), config.load_schema(scripts_dir)
    out: dict = {}
    for section, kv in (changes or {}).items():
        if not isinstance(kv, dict):
            raise ValueError("changes must be {section: {key: value}}")
        for key, value in kv.items():
            if value is not None and not isinstance(value, bool):
                default = base.get(section, {}).get(key)
                kind = schema.get(key, {}).get("type") if section == "gameplay" else None
                if (isinstance(default, float) or kind == "float") and isinstance(value, int):
                    value = float(value)
            out.setdefault(section, {})[key] = value
    return out
