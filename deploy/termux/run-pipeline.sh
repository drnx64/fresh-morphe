#!/data/data/com.termux/files/usr/bin/sh
set -u
HOME=${HOME:-/data/data/com.termux/files/home}
PATH=/data/data/com.termux/files/usr/bin:$PATH
export HOME PATH
cd "$HOME/morphe" || exit 1
mkdir -p logs

lock="$HOME/morphe/.pipeline.lock"
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

printf '\n[%s] pipeline starting\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> logs/pipeline.log
python scripts/run_pipeline.py --db-only >> logs/pipeline.log 2>&1
rc=$?
printf '[%s] pipeline finished rc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$rc" >> logs/pipeline.log
exit "$rc"
