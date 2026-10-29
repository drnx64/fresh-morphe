#!/data/data/com.termux/files/usr/bin/sh
set -u
FRESH_HOME=${FRESH_HOME:-$HOME/fresh-morphe}
if [ ! -d "$FRESH_HOME" ] && [ -d "$HOME/morphe" ]; then
  FRESH_HOME="$HOME/morphe"
fi
cd "$FRESH_HOME" || exit 1
mkdir -p logs bin
termux-wake-lock

api_pids() {
  ps -eo pid,args | awk '/python -m server[.]api/ {print $1}'
}
ngrok_pids() {
  ps -eo pid,args | awk '/[b]in\/ngrok http/ {print $1}'
}
cloudflared_pids() {
  ps -eo pid,args | awk '/[c]loudflared tunnel/ {print $1}'
}

if [ -z "$(api_pids)" ]; then
  nohup python -m server.api --host 127.0.0.1 --port 8090 >>logs/api.log 2>&1 &
fi

i=0
while [ "$i" -lt 30 ]; do
  if curl -fsS -m 2 http://127.0.0.1:8090/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 1
  i=$((i + 1))
done

if [ "${FRESH_WORKER_DISABLED:-${MORPHE_WORKER_DISABLED:-0}}" != "1" ] && { [ -f data/fresh.db ] || [ -f data/morphe.db ]; }; then
  nohup sh bin/worker-loop.sh >/dev/null 2>&1 &
fi

if [ "${FRESH_DISCOVERY_DISABLED:-0}" != "1" ] && [ -f bin/discovery-loop.sh ]; then
  nohup sh bin/discovery-loop.sh >/dev/null 2>&1 &
fi

domain_file="$FRESH_HOME/ngrok-domain.txt"
if [ -s "$domain_file" ] && [ -x "$FRESH_HOME/bin/ngrok" ]; then
  domain=$(head -1 "$domain_file" | tr -d '[:space:]')
  if [ -n "$domain" ]; then
    for pid in $(cloudflared_pids); do kill "$pid" 2>/dev/null || true; done
    if [ -z "$(ngrok_pids)" ]; then
      : > logs/ngrok.log
      nohup "$FRESH_HOME/bin/ngrok" http 8090 --url "https://$domain" --log=stdout --log-format=logfmt >>logs/ngrok.log 2>&1 &
    fi
    printf 'https://%s\n' "$domain" > api-url.txt
    exit 0
  fi
fi

for pid in $(ngrok_pids); do kill "$pid" 2>/dev/null || true; done
if [ -z "$(cloudflared_pids)" ]; then
  : > tunnel.log
  nohup cloudflared tunnel --url http://127.0.0.1:8090 --no-autoupdate >>tunnel.log 2>&1 &
fi

i=0
while [ "$i" -lt 30 ]; do
  url=$(grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' tunnel.log 2>/dev/null | tail -1)
  if [ -n "$url" ]; then
    printf '%s\n' "$url" > api-url.txt
    break
  fi
  sleep 2
  i=$((i + 1))
done
