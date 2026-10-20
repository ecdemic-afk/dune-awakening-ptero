"""Write [world]/[instances]/[gameplay] settings into the game's INI files.

Replaces the old env-variable applier: any of the 241 catalogued settings can
be set from config/dune.toml, not just the 25 that had egg variables. The INI
surgery itself is the core's tested engine (admin_ini_merge).

Runs after prestart.sh has seeded the files. prestart also appends
UserOverrides.ini to UserGame.ini — before this pass — so the merge is redone
here, or a changed override would only take effect one restart later (a bug the
old egg had).
"""
from __future__ import annotations

import os

STATE_REL = os.path.join("server", "state")
FILES = {
    "UserEngine": "ue5-saved/UserSettings/UserEngine.ini",
    "UserGame": "ue5-saved/UserSettings/UserGame.ini",
    "UserOverrides": "UserOverrides.ini",
    "ondemand": "ondemand.ini",
    "UserServerCustomSettings": "ue5-saved/UserSettings/UserServerCustomSettings.ini",
}
# Same markers prestart.sh uses, so the two never append the block twice.
OVR_BEGIN = "; >>>>> AMP: UserOverrides.ini appended below - do not edit past this line >>>>>"
OVR_END = "; <<<<< AMP: end of UserOverrides.ini <<<<<"

MM_SECTION = "/Script/DuneSandbox.MatchmakerEventsSettings"
MM_KEY = "m_BattlegroupsAllMapSettings"
MM_FIRST = '(MapName="DeepDesert_1",MapSettings=(SelectionRule="FirstOfGroup",MaxPlayerCapacity=100,IsStartingMap=False))'
MM_HOME = '(MapName="DeepDesert_1",MapSettings=(SelectionRule="HomeDimension",MaxPlayerCapacity=100,IsStartingMap=False))'


def wanted(cfg: dict, sv: dict) -> dict:
    """setting id -> raw value, from every config section that feeds the INIs."""
    out = {k: v for k, v in cfg["gameplay"].items()
           if k not in ("pvp_partitions", "deep_desert_picker_routing")}
    out["server_display_name"] = sv.get("sietch_name") or cfg["world"]["display_name"] or sv["server_name"]
    out["server_password"] = sv["password"]
    inst = cfg["instances"]
    out["max_concurrent_instances"] = inst["max_concurrent"]
    out["auto_stop_duration"] = inst["auto_stop"]
    out["always_warm_maps"] = ",".join(inst["always_warm"])
    out["pvp_enabled_partitions"] = ",".join(str(p) for p in cfg["gameplay"]["pvp_partitions"])
    return out


def _rw(path: str, transform) -> bool:
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    new = transform(text)
    if new != text:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
    return True


def apply(base: str, cfg: dict, sv: dict, schema: dict, ini, log) -> dict:
    """Write every wanted setting. Returns counts; never raises for one bad row."""
    state = os.path.join(base, STATE_REL)
    stats = {"applied": 0, "failed": 0}
    for sid, value in wanted(cfg, sv).items():
        st = schema.get(sid)
        if st is None:
            continue
        path = os.path.join(state, FILES.get(st.get("file"), ""))
        raw = ("true" if value else "false") if isinstance(value, bool) else value
        try:
            norm = ini.normalize_value(raw, st.get("type", "string"), st.get("enum"))
            if st.get("repeated"):
                vals = [p for p in norm.split(",") if p]
                ok = _rw(path, lambda t: ini.upsert_repeated(t, st["section"], st["key"], vals))
            else:
                rendered = ini.render_value(norm, st.get("quoted", False))
                if rendered is None:
                    raise ValueError("contains a double quote")
                if st.get("section"):
                    ok = _rw(path, lambda t: ini.upsert_keyed(t, st["section"], st["key"], rendered))
                else:
                    ok = _rw(path, lambda t: ini.upsert_flat(t, st["key"], rendered))
            if not ok:
                raise FileNotFoundError(os.path.basename(path))
            stats["applied"] += 1
        except Exception as exc:  # one bad setting must not stop the boot
            stats["failed"] += 1
            log(f"setting {sid} not applied: {exc}", "WARN")
    _picker_routing(state, cfg["gameplay"]["deep_desert_picker_routing"], ini, log)
    if cfg["gameplay"]["pvp_partitions"] and cfg["gameplay"].get("force_pvp_everywhere") is True:
        log("pvp_partitions is ignored by the game while force_pvp_everywhere = true", "WARN")
    merge_overrides(state)
    return stats


def _picker_routing(state: str, enable: bool, ini, log) -> None:
    """DeepDesert_1's default routing ignores the instance a player picks."""
    pair = [f"-{MM_KEY}={MM_FIRST}", f"+{MM_KEY}={MM_HOME}"] if enable else []
    path = os.path.join(state, FILES["UserGame"])
    try:
        _rw(path, lambda t: ini.upsert_matched(t, MM_SECTION, MM_KEY, [MM_FIRST, MM_HOME], pair))
    except Exception as exc:
        log(f"Deep Desert picker routing not applied: {exc}", "WARN")


def merge_overrides(state: str) -> None:
    """Re-append UserOverrides.ini to UserGame.ini between the markers."""
    ug = os.path.join(state, FILES["UserGame"])
    ovr = os.path.join(state, FILES["UserOverrides"])
    if not (os.path.isfile(ug) and os.path.isfile(ovr)):
        return
    with open(ug, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    kept, skipping = [], False
    for line in lines:
        if line == OVR_BEGIN:
            skipping = True
        elif skipping and line == OVR_END:
            skipping = False
        elif not skipping:
            kept.append(line)
    with open(ovr, encoding="utf-8", errors="replace") as fh:
        overrides = fh.read().rstrip("\n")
    with open(ug, "w", encoding="utf-8") as fh:
        fh.write("\n".join(kept + [OVR_BEGIN, overrides, OVR_END]) + "\n")
