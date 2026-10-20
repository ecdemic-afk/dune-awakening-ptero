# Attribution

This project is new code for Pterodactyl — the supervisor (`runtime/`), the egg
(`egg/`, `tools/`), configuration, health checks, backups/restore flow and
Discord alerts — built on a reused low-level core.

## `core/` — reused, MIT
Everything under `core/` is taken from
[Sergentval/pelican-egg-dune-awakening](https://github.com/Sergentval/pelican-egg-dune-awakening)
(MIT), which itself builds on
[CubeCoders/AMPTemplates](https://github.com/CubeCoders/AMPTemplates) (MIT,
© CubeCoders Limited), including fixes made for this project (current game
schema: Solaris enum, deleted-character rows, journey `character_id`,
map-down gate by partition, battlepass offline gate, guild edited-by, base
guard on `actors.state`, ports from the primary allocation):

- `core/mock-k8s/` — open-source Kubernetes API stand-in the Funcom Director talks to.
- `core/scripts/install.sh` — extracts Funcom's OCI images and patches binary loaders.
- `core/scripts/prestart.sh`, `start-*.sh`, `migrate-db.sh`, `lib.sh`, … — service launchers.
- `core/scripts/admin-publish.sh` and helpers — game operations (RabbitMQ commands, DB reads/writes).
- `core/data/admin/` — item, vehicle, skill and settings catalogues.

The upstream license and attribution are kept verbatim in
`core/LICENSE.upstream` and `core/ATTRIBUTION.upstream.md`.

## `docker/Dockerfile`
Adapted from the same upstream image definition (MIT).

## Funcom
Dune: Awakening and its server binaries belong to Funcom. They are downloaded
from Steam at install time and are not part of this repository.

## Map images and calibration

`runtime/dunectl/web/maps/*.webp` and the map bounds in `runtime/dunectl/world.py`
come from the old panel in
[Sergentval/pelican-egg-dune-awakening](https://github.com/Sergentval/pelican-egg-dune-awakening),
which took them from [Icehunter/dune-admin](https://github.com/Icehunter/dune-admin)
(MIT). The Deep Desert image matches the public tile published by th.gl.

## Deep Desert layouts

`core/data/admin/wickmaps.json` (12 Deep Desert POI layouts) and the icons in
`runtime/dunectl/web/maps/icons/` come from the old panel, which ported them from
[coastal-ms/DST-DuneServerTool](https://github.com/coastal-ms/DST-DuneServerTool)
(Apache-2.0, © 2026 Coastal). Layout data is community-collected and may drift.

## Item pictures

Item and vehicle pictures are not shipped: the panel looks them up on
[awakening.wiki](https://awakening.wiki/) (its public API) and caches them in
`server/state/item-images/`. The pictures are the game's own icons, © Funcom.
