#!/data/data/com.termux/files/usr/bin/sh
set -u
FRESH_HOME=${FRESH_HOME:-$HOME/fresh-morphe}
if [ ! -d "$FRESH_HOME" ] && [ -d "$HOME/morphe" ]; then
  FRESH_HOME="$HOME/morphe"
fi
cd "$FRESH_HOME" || exit 1
mkdir -p logs

lock="$FRESH_HOME/.worker.lock"
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

printf '[%s] worker loop starting\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> logs/worker.log
while :; do
  if [ -f data/fresh.db ] || [ -f data/morphe.db ]; then
    python -m server.worker --workers 4 >> logs/worker.log 2>&1
    rc=$?
    printf '[%s] worker finished rc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$rc" >> logs/worker.log
  fi
  sleep 3600
done
