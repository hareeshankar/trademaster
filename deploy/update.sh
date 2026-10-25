#!/bin/bash
# Pull the latest TradeMaster code and restart (run on the server: sudo bash /opt/trademaster/deploy/update.sh)
set -e
OLD=$(git -C /opt/trademaster rev-parse HEAD)
git -C /opt/trademaster pull --ff-only
[ "$OLD" = "$(git -C /opt/trademaster rev-parse HEAD)" ] && [ "${1:-}" != "--force" ] && { echo "already up to date"; exit 0; }
/opt/trademaster/backend/.venv/bin/pip install -q -r /opt/trademaster/backend/requirements.txt
chown -R trademaster:trademaster /opt/trademaster
systemctl restart trademaster
echo "updated: $(git -C /opt/trademaster log -1 --format='%h %s')"
