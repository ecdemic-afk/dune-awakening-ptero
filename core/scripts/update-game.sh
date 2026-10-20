#!/bin/bash
# update-game.sh BASE_DIR
#
# Update the Funcom server build in place — the alternative to Reinstall.
#
# Runs at boot, before any service starts, when either
#   - the AUTO_UPDATE panel variable is 1 (check on every start), or
#   - an update was requested once: `update` in the panel console, or the
#     admin panel's "Update game" button, both drop $STATE/update-requested.
#
# What it touches: the SteamCMD depot (server/ minus server/state/) and the
# extracted OCI trees (extracted/). What it never touches: server/state/ —
# characters, bases, world saves, INIs. Before anything changes it takes a
# cold copy of the game database to backups/pre-update-<ts>.tar.gz (Postgres
# is not running yet at this point, so a file copy is consistent).
#
# Never fails the boot. Any problem is reported and the current build boots;
# Reinstall remains the fallback and is equally safe for saves.
#
# Sourcing this file defines the helpers without running anything, so
# scripts/tests/test-update-game.sh can test them with no SteamCMD.

# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

# Why are we updating? stdout "auto" | "requested"; return 1 = do nothing.
#   $1 AUTO_UPDATE value   $2 request-marker path
update_reason() {
  [ -f "${2:-}" ] && { echo requested; return 0; }
  case "${1:-0}" in 1|true|True|TRUE|yes) echo auto; return 0 ;; esac
  return 1
}

# Did SteamCMD finish the app? Its exit code is not trustworthy (it exits 0 on
# some login/network failures), so read the verdict line from its output.
steamcmd_succeeded() {
  grep -qE "Success! App '[0-9]+' (fully installed|already up to date)" "$1"
}

# Run SteamCMD. The binary is 32-bit. Images with 32-bit libs (docker/Dockerfile
# from this release on) run it the normal way; older images get the i386
# loader + libc the installer bundles into tools/lib32, invoked explicitly.
run_steamcmd() {
  local dir="$BASE/steamcmd" rt="$BASE/tools/lib32" rc tries=0
  [ -x "$dir/steamcmd.sh" ] || { warn "no SteamCMD at $dir — Reinstall once to provision it"; return 1; }
  ulimit -n 2048 2>/dev/null || true
  if [ -e /lib/ld-linux.so.2 ]; then
    HOME="$BASE" "$dir/steamcmd.sh" "$@"
    return
  fi
  if [ ! -x "$rt/ld-linux.so.2" ]; then
    warn "this runtime image has no 32-bit libraries and none were bundled at install."
    warn "Reinstall once (bundles them), or rebuild the image from docker/Dockerfile."
    return 1
  fi
  # A copy of the loader beside the binary: SteamCMD locates its own files from
  # /proc/self/exe, which under an explicit loader is the loader's path.
  cp -f "$rt/ld-linux.so.2" "$dir/linux32/ld-linux.so.2"
  while :; do
    (cd "$dir" && HOME="$BASE" "$dir/linux32/ld-linux.so.2" \
        --library-path "$dir/linux32:$rt" "$dir/linux32/steamcmd" "$@")
    rc=$?
    # 42 = SteamCMD updated itself and asks to be restarted (as steamcmd.sh does).
    [ "$rc" -eq 42 ] && [ $((tries += 1)) -lt 5 ] || return "$rc"
  done
}

# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

main() {
  export SOURCE="update"
  source "$(dirname "$(readlink -f "$0")")/lib.sh" "$@"
  # Build-id helpers only; check-game-build.sh runs nothing when sourced.
  source "$SCRIPTS/check-game-build.sh"

  local marker="$STATE/update-requested" reason appid installed published out url
  reason=$(update_reason "${AUTO_UPDATE:-0}" "$marker") || return 0
  # One shot: a request that fails must not retry (and re-download) every boot.
  rm -f "$marker"

  appid="${SRCDS_APPID:-4754530}"
  installed=$(manifest_buildid "$BASE/server/steamapps/appmanifest_${appid}.acf") || installed=""
  url="${DUNE_BUILD_CHECK_URL:-https://api.steamcmd.net/v1/info/APPID}"
  published=""
  if [ "$url" != off ]; then
    published=$(curl -fsSL --max-time 20 "${url//APPID/$appid}" 2>/dev/null \
      | published_buildid_from_json "$appid") || published=""
  fi

  # AUTO_UPDATE runs on every boot; skip the SteamCMD login when we already
  # know the build is current. An explicit request always runs SteamCMD.
  if [ "$reason" = auto ] && [ -n "$installed" ] && [ "$installed" = "$published" ]; then
    log "game build $installed is current — nothing to update"
    return 0
  fi
  log "updating game files ($reason): installed ${installed:-unknown}, published ${published:-unknown}"

  # install.sh ignores patchelf failures per binary, so a patchelf that cannot
  # run here would leave a build that does not start. Check before downloading.
  export PATH="$PATH:$BASE/tools/bin"
  if ! patchelf --version >/dev/null 2>&1; then
    warn "patchelf is not usable in this image — Reinstall to update, or rebuild the image from docker/Dockerfile"
    return 1
  fi

  if [ -f "$STATE/pg/data/PG_VERSION" ]; then
    local ts; ts=$(date -u +%Y%m%d-%H%M%S)
    mkdir -p "$BASE/backups"
    log "backing up the game database to backups/pre-update-$ts.tar.gz ..."
    if ! tar -czf "$BASE/backups/pre-update-$ts.tar.gz" -C "$STATE" pg/data; then
      rm -f "$BASE/backups/pre-update-$ts.tar.gz"
      warn "database backup failed (disk full?) — NOT updating; booting the current build"
      return 1
    fi
    # Keep the newest 3.
    ls -1t "$BASE"/backups/pre-update-*.tar.gz 2>/dev/null | tail -n +4 | xargs -r rm -f
  fi

  out="$LOGS/steamcmd-update.log"
  log "running SteamCMD (output in logs/steamcmd-update.log) ..."
  run_steamcmd +@sSteamCmdForcePlatformType linux \
      +force_install_dir "$BASE/server" \
      +login "${STEAM_USER:-anonymous}" "${STEAM_PASS:-}" "${STEAM_AUTH:-}" \
      +app_update "$appid" \
      +quit > "$out" 2>&1 || true
  if ! steamcmd_succeeded "$out"; then
    tail -n 15 "$out" | while IFS= read -r l; do warn "  steamcmd: $l"; done
    warn "SteamCMD did not finish — booting the current build (extracted/ untouched)"
    return 1
  fi

  # Re-extract only the images whose tarball changed (install.sh is idempotent).
  bash "$SCRIPTS/install.sh" "$BASE" \
    || { warn "re-extracting the new build failed — if the server will not start, Reinstall"; return 1; }

  log "game build now $(manifest_buildid "$BASE/server/steamapps/appmanifest_${appid}.acf" || echo unknown)"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
