#!/data/data/com.termux/files/usr/bin/sh
# Fresh Morphe discovery loop.
#
# Runs source discovery on its own schedule so a slow GitHub search never blocks
# the fetch/parse/diff pipeline or the incremental worker. Holds .discovery.lock
# for its lifetime, so only one instance ever runs.
set -u

HOME=${HOME:-/data/data/com.termux/files/home}
PATH=/data/data/com.termux/files/usr/bin:$PATH
export HOME PATH

FRESH_HOME=${FRESH_HOME:-$HOME/fresh-morphe}
if [ ! -d "$FRESH_HOME" ] && [ -d "$HOME/morphe" ]; then
  FRESH_HOME="$HOME/morphe"
fi
cd "$FRESH_HOME" || exit 1
mkdir -p logs

lock="$FRESH_HOME/.discovery.lock"
if ! mkdir "$lock" 2>/dev/null; then
  pid=$(cat "$lock/pid" 2>/dev/null || true)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    exit 0
  fi
  rm -rf "$lock"
  mkdir "$lock" 2>/dev/null || exit 0
fi
printf '%s\n' "$$" > "$lock/pid"

cleanup() {
  if [ "$(cat "$lock/pid" 2>/dev/null || true)" = "$$" ]; then
    rm -rf "$lock"
  fi
}
trap cleanup EXIT INT TERM

# Every 6 hours by default. DISCOVERY_SEARCH_PAGES controls GitHub depth.
interval=${DISCOVERY_INTERVAL_SECONDS:-21600}
export DISCOVERY_INTERVAL_SECONDS="$interval"

printf '[%s] discovery loop starting (interval %ss)\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$interval" >> logs/discovery.log

while :; do
  printf '[%s] discovery run starting\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> logs/discovery.log
  python scripts/discover_sources.py >> logs/discovery.log 2>&1
  rc=$?
  printf '[%s] discovery run finished rc=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$rc" >> logs/discovery.log
  sleep "$interval"
done
