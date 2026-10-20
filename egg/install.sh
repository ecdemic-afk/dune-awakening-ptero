#!/bin/bash
# Dune: Awakening — Pterodactyl install / reinstall.
#
# Runs in the install container (root, server files at /mnt/server, which is
# /home/container at runtime). Safe to re-run: saves live in server/state/ and
# config/, which this script never deletes, and the database is copied to
# backups/ first.
#
#   1. tools        2. safety backup      3. game files (SteamCMD)
#   4. this project (scripts, runtime, Kubernetes stand-in)
#   5. update tools for in-place updates   6. unpack the game's images
set -euo pipefail

REPO="${SOURCE_REPO:-ecdemic-afk/dune-awakening-ptero}"
REF="${SOURCE_REF:-main}"
case "${BRANCH:-live}" in
    ptc|PTC) APPID=3104830 ;;
    *)       APPID=4754530 ;;
esac
S=/mnt/server
step() { echo; echo "==> $*"; }

step "Installing tools"
export DEBIAN_FRONTEND=noninteractive
dpkg --add-architecture i386
apt-get update -q
apt-get install -y -q --no-install-recommends \
    ca-certificates curl tar gzip jq file lib32gcc-s1 libcurl4:i386 patchelf \
    iproute2 gawk python3
mkdir -p "$S/steamcmd" "$S/server" "$S/backups" "$S/config"

if [ -f "$S/server/state/pg/data/PG_VERSION" ]; then
    step "Backing up the game database (saves are never deleted by a reinstall)"
    ts=$(date -u +%Y%m%d-%H%M%S)
    tar -czf "$S/backups/pre-reinstall-$ts.tar.gz" -C "$S/server/state" pg/data
    ls -1t "$S"/backups/pre-reinstall-*.tar.gz | tail -n +4 | xargs -r rm -f
fi

step "Downloading the game server (Steam app $APPID, ~5 GB)"
curl -fsSL https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz | tar -xz -C "$S/steamcmd"
cd "$S/steamcmd"
# A fresh SteamCMD often fails its first app_update with "Missing
# configuration" (app info not loaded yet in that session). Refresh the app
# info and retry; success is SteamCMD's own "Success!" line, not a folder
# that a previous partial download may have left behind.
steam_ok=0
for attempt in 1 2 3; do
    HOME="$S" ./steamcmd.sh +@sSteamCmdForcePlatformType linux +force_install_dir "$S/server" \
        +login anonymous +app_info_update 1 +app_update "$APPID" validate +quit \
        2>&1 | tee "$S/steamcmd-install.log" || true
    if grep -qE "Success! App '$APPID' (fully installed|already up to date)" "$S/steamcmd-install.log"; then
        steam_ok=1; break
    fi
    if [ "$attempt" -lt 3 ]; then
        echo "SteamCMD attempt $attempt did not finish — retrying in 20 seconds..."; sleep 20
    fi
done
if [ "$steam_ok" != 1 ] || [ ! -d "$S/server/images" ]; then
    echo "ERROR: the game download failed after 3 attempts (see SteamCMD's output above," >&2
    echo "       also saved to steamcmd-install.log). Run Reinstall to try again." >&2
    exit 1
fi

step "Downloading $REPO@$REF"
SRC=$(mktemp -d)
curl -fsSL "https://codeload.github.com/$REPO/tar.gz/$REF" | tar -xz -C "$SRC"
SRC=$(echo "$SRC"/*)
[ -d "$SRC/core/scripts" ] && [ -d "$SRC/runtime" ] || {
    echo "ERROR: $REPO@$REF does not look like this project — check Source Repository / Source Version" >&2; exit 1; }
rm -rf "$S/scripts" "$S/data" "$S/runtime" "$S/mock-k8s"
cp -r "$SRC/core/scripts" "$S/scripts"
cp -r "$SRC/core/data" "$S/data"
cp -r "$SRC/runtime" "$S/runtime"
cp -r "$SRC/core/mock-k8s" "$S/mock-k8s"
[ -f "$S/.pteroignore" ] || cp "$SRC/egg/pteroignore" "$S/.pteroignore"
chmod +x "$S"/scripts/*.sh "$S"/runtime/dunectl.py

step "Building the Kubernetes stand-in (mock-k8s)"
GO_VERSION="${GO_VERSION:-1.22.7}"
curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" | tar -xz -C /usr/local
# Build scratch on the server disk: Wings' /tmp is a small tmpfs.
export PATH=/usr/local/go/bin:$PATH GOPATH="$S/.gobuild/path" GOCACHE="$S/.gobuild/cache" GOTMPDIR="$S/.gobuild/tmp"
mkdir -p "$GOPATH" "$GOCACHE" "$GOTMPDIR"
(cd "$S/mock-k8s" && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o "$S/scripts/mock-k8s-go" ./cmd/mock-k8s)
rm -rf "$S/.gobuild" /usr/local/go "$SRC"

step "Bundling the tools for in-place game updates"
rm -rf "$S/tools" && mkdir -p "$S/tools/bin" "$S/tools/lib32"
cp -L /usr/bin/patchelf "$S/tools/bin/patchelf"
[ -e /usr/lib32/ld-linux.so.2 ] && cp -rL /usr/lib32/. "$S/tools/lib32/"

step "Unpacking the game's images (several minutes, little output)"
cd "$S"
ln -snfr server depot
bash scripts/install.sh "$S"

step "Install complete — press Start."
