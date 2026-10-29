#!/data/data/com.termux/files/usr/bin/sh
# Fresh Morphe watchdog: revive a dead API, tunnel, or worker loop.
# Runs from cron; exits quietly when everything is healthy.
set -u

FRESH_HOME=${FRESH_HOME:-$HOME/fresh-morphe}
if [ ! -d "$FRESH_HOME" ] && [ -d "$HOME/morphe" ]; then
  FRESH_HOME="$HOME/morphe"
fi
cd "$FRESH_HOME" || exit 1
mkdir -p logs

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> logs/watchdog.log
}

api_alive() {
  curl -fsS -m 5 http://127.0.0.1:8090/healthz >/dev/null 2>&1
}

ngrok_alive() {
  ps -eo args | grep -q '[b]in/ngrok http'
}

worker_loop_alive() {
  ps -eo args | grep -q '[w]orker-loop\.sh'
}

restart=0

if ! api_alive; then
  log "api down"
  restart=1
fi

if ! ngrok_alive; then
  log "ngrok down"
  restart=1
fi

if ! worker_loop_alive; then
  log "worker loop down"
  # A dead loop leaves its lock behind and would block the restart.
  lock="$FRESH_HOME/.worker.lock"
  if [ -d "$lock" ]; then
    pid=$(cat "$lock/pid" 2>/dev/null || true)
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
      rm -rf "$lock"
    fi
  fi
  restart=1
fi

if [ "$restart" = "1" ]; then
  log "starting stack"
  sh bin/start-stack.sh >> logs/watchdog.log 2>&1
  log "start-stack rc=$?"
fi

exit 0
