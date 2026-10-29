#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
sleep 15
if [ -f "$HOME/fresh-morphe/bin/start-stack.sh" ]; then
  exec sh "$HOME/fresh-morphe/bin/start-stack.sh"
fi
exec sh "$HOME/morphe/bin/start-stack.sh"
