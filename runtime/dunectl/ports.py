"""Every public port, computed from the primary allocation.

Pterodactyl hands the container only its primary port (SERVER_PORT); extra
allocations are invisible from inside. So instead of one variable per port, the
operator allocates one contiguous block and everything is an offset into it.
An allocation covers TCP and UDP alike, so TCP services can sit inside the block.
"""
from __future__ import annotations

# Ports the core binds on loopback inside the container. A public port equal to
# one of these makes one of the two fail with an error that points elsewhere.
INTERNAL_PORTS = {15432, 5672, 15672, 15693, 5059, 11717, 8080, 6443, 8443, 8089,
                  4369, 25672, 25673}


def layout(server_port: int, ports_cfg: dict, dimensions: int) -> dict:
    base = int(server_port)
    game_slots = ports_cfg["game_slots"]
    return {
        "game_base": base,
        "game_slots": game_slots,
        "dim_base": base + game_slots,
        "dimensions": dimensions,
        "mq_game": base + ports_cfg["mq_game_offset"],
        "mq_mgmt": base + ports_cfg["mq_mgmt_offset"],
        "panel": base + ports_cfg["panel_offset"],
        "block_first": base,
        "block_last": base + ports_cfg["block_size"] - 1,
    }


def problems(lay: dict) -> list[str]:
    out = []
    if lay["block_last"] > 65535:
        out.append(f"the port block ends at {lay['block_last']}, above 65535 — use a lower primary allocation")
    for name in ("mq_game", "mq_mgmt", "panel"):
        if lay[name] in INTERNAL_PORTS:
            out.append(f"{name} port {lay[name]} is used inside the container — choose another primary allocation or offset")
    return out


def describe(lay: dict) -> list[str]:
    """Lines for the boot log: what to allocate and forward."""
    udp_last = lay["dim_base"] + max(lay["dimensions"], 1) - 1
    return [
        f"Allocate ports {lay['block_first']}-{lay['block_last']} to this server (one block).",
        f"Forward on your router: UDP {lay['game_base']}-{udp_last}, "
        f"TCP {lay['mq_game']} and {lay['mq_mgmt']}"
        + f". The web panel is on TCP {lay['panel']}: forward it only to reach the panel from outside "
          "your network (it has a password but no HTTPS).",
    ]
