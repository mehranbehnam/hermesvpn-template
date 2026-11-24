#!/bin/bash
set -e
STAMP=$(date +%Y%m%d-%H%M%S)
TMPDIR=$(mktemp -d)

cp /etc/x-ui/x-ui.db "$TMPDIR/x-ui-$STAMP.db"
cp /root/.hermes-orders.db "$TMPDIR/orders-$STAMP.db"

# full disaster-recovery bundle: bot code + systemd units + tokens/secrets +
# security config, so a restore doesn't depend on this server staying reachable
tar -czf "$TMPDIR/hermesvpn-code-$STAMP.tar.gz" \
  /usr/local/bin/hermes-bot.py /usr/local/bin/hermes-customer-bot.py /usr/local/bin/hermesvpn-monitor.py \
  /usr/local/bin/hermesvpn-backup.sh /usr/local/bin/vpn-watchdog.sh \
  /usr/local/bin/hermesvpn-daily-audit.py /usr/local/bin/hermesvpn-health-check.py \
  /usr/local/bin/hermesvpn-backfill-traffic.py /usr/local/bin/hermesvpn-note.sh \
  /root/HERMESVPN-INFRASTRUCTURE-HANDOFF.md \
  /etc/systemd/system/hermes-bot.service /etc/systemd/system/hermes-customer-bot.service \
  /etc/systemd/system/hermesvpn-monitor.service /etc/systemd/system/hermesvpn-monitor.timer \
  /etc/systemd/system/hermesvpn-daily-audit.service /etc/systemd/system/hermesvpn-daily-audit.timer \
  /etc/systemd/system/vpn-watchdog.service /etc/systemd/system/vpn-watchdog.timer \
  /root/.hermes-bot-token /root/.hermes-customer-bot-token /root/.hermes-paid-webhook-secret \
  /root/.secrets/cloudflare.ini /etc/fail2ban/jail.local /etc/sysctl.d/99-bbr.conf \
  2>/dev/null || true

tar -czf "$TMPDIR/hermesvpn-backup-$STAMP.tar.gz" -C "$TMPDIR" "x-ui-$STAMP.db" "orders-$STAMP.db" "hermesvpn-code-$STAMP.tar.gz"

timeout 30 scp -O -i /root/.ssh/backup_to_hetzner -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
  "$TMPDIR/hermesvpn-backup-$STAMP.tar.gz" root@203.0.113.20:/root/backups/oracle/

rm -rf "$TMPDIR"
echo "$(date): backup $STAMP sent" >> /var/log/hermesvpn-backup.log
