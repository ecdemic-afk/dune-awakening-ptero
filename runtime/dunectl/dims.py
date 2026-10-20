"""Keep the number of running map copies ("dimensions") at what the config says.

The core seeds one world_partition row per copy and starts every row at boot;
lowering a count never removed rows, so a server that once had 3 Deep Desert
copies kept starting 3 game servers (each several GB of RAM) whatever the
config said. Copies above the configured count are PARKED instead — their row
and data stay, they just don't start — and un-parked when the count goes up.

Survival_1 copies are player sietches (bases live there) and are never touched.
"""
from __future__ import annotations

COUNT_KEY = {
    "DeepDesert_1": "deep_desert_dimensions",
    "SH_Arrakeen": "arrakeen_dimensions",
    "SH_HarkoVillage": "harko_dimensions",
    "SH_FallenLight": "fallen_light_dimensions",
}


def wanted_count(cfg: dict, map_name: str) -> int | None:
    key = COUNT_KEY.get(map_name)
    if key is None:
        return None  # not ours to manage (e.g. Survival_1 sietches)
    inst = cfg["instances"]
    if map_name == "SH_FallenLight" and not inst["fallen_light"]:
        return 0
    return int(inst[key])


def plan(rows: list[tuple[int, str, int]], cfg: dict) -> tuple[set[int], set[int]]:
    """rows: (partition_id, map, dimension_index). Returns (park, unpark)."""
    park, unpark = set(), set()
    for pid, map_name, dim in rows:
        want = wanted_count(cfg, map_name)
        if want is None or dim <= 0:
            continue
        (park if dim > want else unpark).add(pid)
    return park, unpark


def parse_rows(csv_text: str) -> list[tuple[int, str, int]]:
    """CSV from db-sql: header line, then partition_id,map,dimension_index."""
    rows = []
    for line in csv_text.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[0].isdigit() and parts[2].lstrip("-").isdigit():
            rows.append((int(parts[0]), parts[1], int(parts[2])))
    return rows
