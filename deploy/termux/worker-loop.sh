#!/data/data/com.termux/files/usr/bin/sh
set -u
cd "$HOME/morphe" || exit 1
mkdir -p logs

lock="$HOME/morphe/.worker.lock"
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
  if [ -f data/morphe.db ]; then
    python -m server.worker --workers 4 >> logs/worker.log 2>&1
    rc=$?
    printf '[%s] worker finished rc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$rc" >> logs/worker.log
  fi
  sleep 3600
done
