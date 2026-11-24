#!/bin/bash
LOG=/var/log/x-ui/watchdog.log
ts() { date "+%Y-%m-%d %H:%M:%S"; }

ok=true
for port in 443 8443; do
  if ! timeout 3 bash -c "echo > /dev/tcp/127.0.0.1/$port" 2>/dev/null; then
    ok=false
    echo "$(ts) port $port not responding" >> "$LOG"
  fi
done

if ! systemctl is-active --quiet x-ui; then
  ok=false
  echo "$(ts) x-ui service is not active" >> "$LOG"
fi

if [ "$ok" = false ]; then
  echo "$(ts) restarting x-ui" >> "$LOG"
  systemctl restart x-ui
fi
