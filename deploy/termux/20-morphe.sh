#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
sleep 15
exec sh "$HOME/morphe/bin/start-stack.sh"
