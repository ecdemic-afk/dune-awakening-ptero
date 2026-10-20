#!/bin/bash
# apply-refill-queue.sh BASE_DIR
#
# Apply base water/fuel refills queued from the admin panel. A running map
# keeps its bases in memory and writes them back over any DB edit, and the
# main Hagga Basin sietch can never be stopped while the server runs — so the
# panel queues the refill and this boot stage applies it: after Postgres and
# the migrations are up, before mock-k8s starts any map.
#
# Queue: $STATE/refill-queue, one "<water|fuel> <base_id>" per line. One-shot:
# the file is removed whether or not each refill worked (results are logged),
# so a refill that keeps failing never repeats on every boot. Never blocks the
# boot.

export SOURCE="refill-queue"
source "$(dirname "$(readlink -f "$0")")/lib.sh" "$@"

QUEUE="$STATE/refill-queue"
[ -s "$QUEUE" ] || exit 0

mv -f "$QUEUE" "$QUEUE.applying"
while read -r kind bid _; do
  case "$kind" in water|fuel) ;; *) continue ;; esac
  case "$bid" in ''|*[!0-9]*) continue ;; esac
  log "refilling $kind for base #$bid (queued from the admin panel)"
  if out=$(DUNE_MAPS_NOT_STARTED=1 bash "$SCRIPTS/admin-publish.sh" "base-$kind-refill" "$bid" 2>&1); then
    log "  done: $(printf '%s' "$out" | grep -m1 'OK' || echo ok)"
  else
    warn "  failed: $(printf '%s' "$out" | tail -n1)"
  fi
done < "$QUEUE.applying"
rm -f "$QUEUE.applying"
exit 0
