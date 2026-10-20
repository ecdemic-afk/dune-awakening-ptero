# Dune: Awakening for Pterodactyl

Run a full Dune: Awakening battlegroup on **Pterodactyl Panel + Wings** — no
Windows, no Hyper-V, no Kubernetes.

Built to be easy to run:

- **18 startup variables**, not 60+ — including which maps run and which stay
  cold. Everything else lives in one commented
  file, `config/dune.toml`, that you edit in Pterodactyl's File Manager. A typo
  is reported at boot with its line number instead of being silently ignored.
- **One port block.** Allocate 40 consecutive ports starting at the primary
  allocation; every port is worked out from there. The console prints exactly
  what to allocate and forward.
- **Restarts and game updates happen in place** — no Reinstall, no Pterodactyl
  API key. Type `restart` or `update` (or let a Pterodactyl Schedule do it);
  players get an in-game countdown when someone is online.
- **Health checks at every boot** with the fix spelled out: bad token, wrong
  public IP, Wings' thread limit, memory, disk, CPU, missing files.
- **Self-healing**: a crashed service triggers an automatic restart; a problem
  a restart cannot fix stops and waits for you instead of looping.
- **Backups you can restore**: `backup` dumps the database safely while running;
  `restore <file> confirm` puts it back (the current world is saved first).
- **Discord**: one live status message, edited every minute — online / offline,
  players on each map instance, upcoming restarts — plus alerts for crashes,
  updates, backups and health.
- **Web panel**: status, players online, health, restart/update/backup buttons,
  live console, config editor that refuses mistakes, one-click restore.

> Phase 1 of 4. Coming next: player tools by name (history, notes, kits), the
> web panel, Discord commands. See [Roadmap](#roadmap).

---

## Install

### 1. Prepare the Wings machine (once)
In `/etc/pterodactyl/config.yml` set `container_pid_limit: 4096` under
`docker:`, then `systemctl restart wings`. The default (512) makes the game's
services die with a misleading "Out of memory". The health check warns if this
is still low.

Hardware per server: 6+ CPU cores with AVX2, 32 GB RAM (64 recommended), 60 GB disk.

### 2. Get a Self-Host Service Token
Sign in at [account.duneawakening.com](https://account.duneawakening.com/) with
the Steam account that owns the game. **Every running server needs its own token.**

### 3. Import the egg
Admin → Nests → **Import Egg** → `egg/egg-dune-awakening.json`.

### 4. Allocate one port block
Admin → Nodes → your node → Allocation: add **40 consecutive ports**, e.g.
`27000-27039`. Any free range works.

### 5. Create the server
Admin → Servers → Create New:

| Field | Value |
|---|---|
| Default allocation | the **first** port of the block (`27000`) |
| Additional allocations | the rest of the block (`27001-27039`) |
| Egg | Dune: Awakening |
| Memory | `0` (unlimited), untick the OOM killer |
| Disk | `60000` MB or more |
| Startup → Self-Host Service Token | your token |
| Startup → Server Name / Password / Region | your choice |

Leave **Public IP** on `auto` unless the health check says otherwise.

The install downloads ~5 GB and unpacks the game: 20-60 minutes.

### 6. Forward ports and start
On your router forward the **UDP** game range and the two **TCP** broker ports
the console prints at boot (`Forward on your router: …`). Press **Start** and
wait for `[dune] READY`.

---

## Everyday use — console commands

Type these in Pterodactyl's **Console** tab:

| Command | What it does |
|---|---|
| `status` | services, game servers, players online, pending actions |
| `doctor` | health check with fixes |
| `restart` | restart with the configured countdown (no countdown if nobody is on) |
| `restart in 15m Nightly restart` | countdown of your choice, with a reason |
| `restart now` | immediately |
| `update` / `update now` | install a game update, then restart |
| `upgrade` / `upgrade now` | install the newest version of **this server software** from GitHub, then restart — no Reinstall |
| `cancel` | cancel a pending restart or update |
| `announce <message>` | banner to everyone in game |
| `backup` | save the database to `backups/` |
| `restore` / `restore <file> confirm` | list backups / restore one |
| `players` | list players |
| `config` | check `config/dune.toml` for mistakes |
| `ports` | ports to allocate and forward |
| `logs verbose` / `logs quiet` | all service output / warnings only (default) |
| `admin <op> …` | raw game operation for advanced use |
| `stop` | stop everything (Pterodactyl's Stop button does this) |

A **real** Pterodactyl restart is only needed after changing a Startup tab
variable. For everything in `config/dune.toml`, `restart` is enough.

### Memory: which maps run and which stay cold
Each running map is a full game server using ~5 GB of RAM. Set it on the
**Startup** tab, then press Pterodactyl's **Restart** button:

| Variable | Default | Meaning |
|---|---|---|
| Always-Running Maps | `Survival_1,Overmap,DeepDesert_1` | maps that never stop |
| Max Running Maps | `8` | most maps up at once (on-demand ones wait beyond this) |
| Stop Empty Maps After | `10m` | how long a cold map stays up after its last player leaves |
| Deep Desert / Arrakeen / Harko Copies | `1` / `0` / `0` | extra always-running instances players can pick |

Every map not in the list (dungeons, story maps, Arrakeen, Harko…) stays cold
until a player enters it. Clearing a variable falls back to `[instances]` in
`config/dune.toml`. The health check (**Map budget**) says whether that fits
in the server's memory. Lowering a copy count keeps those copies' data and just
stops starting them; raising it brings them back.

## Web panel

Set **Web Panel Password** (10+ characters) on the Startup tab and press
Restart. Open `http://<server IP>:<primary port + 32>` — the console prints the
address at start (with the default block, `27000` → `27032`).

- **Overview**: state, players, whether players can join yet, health checks,
  services, and buttons for restart / game update / software upgrade / backup /
  announcement. Restarts use the in-game countdown when players are on.
- **Console**: the same output as Pterodactyl's console, and a command box.
- **Config**: edit `config/dune.toml`; a file with a mistake is refused with the
  line and the fix, so a typo can't stop the server.
- **Players**: every player by name, online first. Open one to see level, Solaris,
  reputation, location and inventory, and to kick, ban (for hours or for good),
  refill water, give items (search the 2,500-item catalogue) or a kit, adjust
  Solaris / XP / reputation, rename, unlock keystones or recipes, and edit or
  delete inventory items. Changes the game only allows offline say so.
The panel has five sections: **Overview**, **Players** (Players · Items · Vehicles · Kits & gifts),
**World** (Map · Bases · Guilds), **Server** (Settings · Schedule · Backups ·
Commands) and **Console**.

- **Settings**: every option of `config/dune.toml` as switches, number fields and
  lists, with its explanation, limits and default, grouped by section. The game's
  own settings (about 220) can be searched and filtered by category; changed ones
  are marked and **Default** undoes them. Saving changes only those lines in the
  file — your comments stay — and a mistake is refused before anything is saved.
  **Edit as text** still shows the whole file.

- **Items**: the whole catalogue with pictures, by category or search; give any
  item to a player or to everyone online, or add it to a kit. Pictures come from
  the awakening.wiki community wiki: the server fetches each one once and keeps
  it, so the first view of a category fills in over a few seconds.
- **Vehicles**: sandbikes, buggies, tanks, sandcrawlers, ornithopters, treadwheels,
  each with its variants; it spawns a few metres from the chosen online player —
  no coordinates to type.
- **Kits & gifts**: create and edit kits; give a kit or any item to one player or
  to everyone online; a **welcome gift** every player gets once (new players as
  they arrive), optionally announced.
- **Map**: Hagga Basin, Deep Desert, Arrakeen and Harko Village, with a dot per
  player (offline players on request). Click a dot to open the player; click
  anywhere else to teleport a player who is on that map there (the game puts them
  on the nearest safe spot). The Deep Desert shows its sector grid and this week's
  resources — caves, wrecks, testing stations, spice fields, titanium,
  stravidium, taxis — from the 12 known layouts. **Saved locations**: save a
  clicked spot or where a player stands, then send any player on that map there.
  Refreshes every 10 s.
- **Bases**: every base with its owner and map; water storage and generator fuel
  levels; refill water or fuel. A running map keeps its bases in memory, so the
  refill is queued and applied at the next restart before the map loads.
- **Guilds**: members and invites; description, rename, change leader, remove a
  member, create, disband.
- **Schedule**: restarts at set times and days (with countdown and optional game
  update), database backups every N hours, timed announcements. Runs inside the
  server — no Pterodactyl API key.
- **Commands**: buttons for the server commands, and every game operation as a
  form (player fields pick from the player list; destructive ones ask you to
  type their name).
- **Backups**: back up now, or restore one (the current world is saved first).

The panel is up even while the server waits for a config fix. It has a password
but no HTTPS: keep its port on your network, or put it behind a reverse proxy
with HTTPS before forwarding it. Five wrong passwords lock that address out for
a minute.

## Schedules (Pterodactyl → server → Schedules)

| Goal | Cron | Tasks |
|---|---|---|
| Nightly restart (also installs game updates when Auto Update = 1) | `0 4 * * *` | Send command: `restart in 10m Nightly restart` |
| Backup every 6 hours | `0 */6 * * *` | 1. Send command: `backup` · 2. Create backup (time offset 120 s) |
| Announcement | any | Send command: `announce Double XP this weekend!` |

### About Pterodactyl Backups
`.pteroignore` (created at install) makes Pterodactyl Backups store your
**config, world saves and `backups/` dumps** but not the 12 GB of game files or
the live database files — that is why a schedule runs `backup` first.
When restoring a Pterodactyl backup, **do not tick "delete all files"**: the game
files are not in the backup. After restoring, use `restore <file> confirm` to
load a database dump.

## Configuration — `config/dune.toml`

Created on first boot with every option explained. Highlights:

```toml
[instances]
max_concurrent = 8
deep_desert_dimensions = 1

[gameplay]          # any of the 241 server settings, by id
mining_output = 2.0
sandworms_enabled = true
pvp_partitions = [8]

[updates]
warn_minutes = 10   # countdown before restart/update when players are on
```

The full settings list, with defaults, is `data/admin/settings-schema.json`.
Save, then type `restart`. If the file has a mistake the server waits for the
fix (and notices when you save it).

## When something goes wrong

- The console shows warnings and errors from every service; `logs verbose`
  shows everything. Full logs are in `logs/`.
- `doctor` re-runs every health check.
- After a crash the server restarts itself (up to 3 times in 30 minutes, set in
  `[advanced]`), then waits for you with the reason on screen — and in Discord.
- "Out of memory" from director/text-router while RAM is free = Wings' thread
  limit (step 1 of Install).
- Players get "Outdated Client" = a game update is out: `update`.

## Roadmap

1. **Foundation** — this release.
2. **Players** — search, history, notes, kits, give/teleport/kick/ban by name.
3. **Web panel** — overview, players, map, bases, guilds, schedule, commands,
   console, config and backups are in; next: Deep Desert and instances, then
   economy and events (market, loot, spice), then database tools.
4. **Discord commands**.

## The server image

The egg runs on `ghcr.io/ecdemic-afk/dune-awakening-ptero:latest`, built from
`docker/Dockerfile` by GitHub Actions on every change there and weekly for Debian
security fixes. It holds Debian and a few packages only; the game is downloaded at
install time.

## Credits

The low-level layer that makes Funcom's servers run on Linux without Kubernetes
(`core/`) comes from
[Sergentval/pelican-egg-dune-awakening](https://github.com/Sergentval/pelican-egg-dune-awakening)
and [CubeCoders' AMPTemplates](https://github.com/CubeCoders/AMPTemplates), both
MIT. See [ATTRIBUTION.md](ATTRIBUTION.md). The game binaries are Funcom's,
downloaded from Steam at install time.
