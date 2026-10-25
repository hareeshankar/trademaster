#!/bin/bash
# Pull the latest TradeMaster code and restart (run on the server: sudo bash /opt/trademaster/deploy/update.sh)
set -e
git config --system --add safe.directory /opt/trademaster 2>/dev/null || true   # repo is owned by the app user
# keep the auto-deploy schedule current (every 2 minutes)
echo '*/2 * * * * root flock -n /run/tm-update.lock bash /opt/trademaster/deploy/update.sh >> /var/log/trademaster-update.log 2>&1' > /etc/cron.d/trademaster-update
OLD=$(git -C /opt/trademaster rev-parse HEAD)
git -C /opt/trademaster pull -q --ff-only
[ "$OLD" = "$(git -C /opt/trademaster rev-parse HEAD)" ] && [ "${1:-}" != "--force" ] && exit 0
/opt/trademaster/backend/.venv/bin/pip install -q -r /opt/trademaster/backend/requirements.txt
chown -R trademaster:trademaster /opt/trademaster
systemctl restart trademaster
echo "updated: $(git -C /opt/trademaster log -1 --format='%h %s')"
