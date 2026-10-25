#!/bin/bash
# TradeMaster one-shot server install (Ubuntu 22.04/24.04, x86 or ARM). Run as root. Safe to run again.
set -euo pipefail
REPO="${TM_REPO:-https://github.com/hareeshankar/trademaster.git}"
APP=/opt/trademaster
ENVF=/etc/trademaster.env
export DEBIAN_FRONTEND=noninteractive

echo ">> packages"
apt-get update -y
apt-get install -y python3-venv python3-pip git curl debian-keyring debian-archive-keyring apt-transport-https iptables-persistent
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y && apt-get install -y caddy
fi

echo ">> firewall: open 80 + 443 (Oracle images block them by default)"
for p in 80 443; do
  iptables -C INPUT -p tcp --dport $p -j ACCEPT 2>/dev/null || iptables -I INPUT 5 -p tcp --dport $p -j ACCEPT
done
netfilter-persistent save

echo ">> swap (small servers)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo ">> clock: India time"
timedatectl set-timezone Asia/Kolkata || true

echo ">> app"
git config --system --add safe.directory /opt/trademaster
id trademaster >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin trademaster
if [ -d "$APP/.git" ]; then git -C "$APP" pull --ff-only; else git clone --depth 1 "$REPO" "$APP"; fi
python3 -m venv "$APP/backend/.venv"
"$APP/backend/.venv/bin/pip" install -q --upgrade pip
"$APP/backend/.venv/bin/pip" install -q -r "$APP/backend/requirements.txt"
mkdir -p /var/lib/trademaster && chown -R trademaster:trademaster /var/lib/trademaster "$APP"

if [ ! -f "$ENVF" ]; then
  cat > "$ENVF" <<ENV
TM_DATA_DIR=/var/lib/trademaster
TM_SECRET_KEY=$(head -c 32 /dev/urandom | base64 | tr -d '=+/')
TM_ALLOW_LIVE=1
TM_CORS_ORIGINS=http://localhost:5173
ENV
  chmod 600 "$ENVF"
fi

cat > /etc/systemd/system/trademaster.service <<UNIT
[Unit]
Description=TradeMaster trading engine + screens
After=network-online.target
Wants=network-online.target
[Service]
User=trademaster
EnvironmentFile=$ENVF
WorkingDirectory=$APP/backend
ExecStart=$APP/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT

echo ">> HTTPS address from the public IP (<ip-with-dashes>.sslip.io)"
cat > /usr/local/bin/trademaster-https <<'SH'
#!/bin/bash
IP=$(curl -s --max-time 10 https://api.ipify.org || true)
[[ "$IP" =~ ^[0-9.]+$ ]] || exit 0
HOST="${IP//./-}.sslip.io"
NEW="$HOST {
	encode gzip
	reverse_proxy 127.0.0.1:8000
}"
if [ "$(cat /etc/caddy/Caddyfile 2>/dev/null)" != "$NEW" ]; then
  echo "$NEW" > /etc/caddy/Caddyfile
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
fi
echo "https://$HOST" > /etc/trademaster.url
SH
chmod +x /usr/local/bin/trademaster-https
echo '*/5 * * * * root /usr/local/bin/trademaster-https' > /etc/cron.d/trademaster-https

echo ">> nightly backup of strategies + trades (keeps 14 days)"
echo '30 23 * * * root tar -czf /var/backups/trademaster-$(date +\%F).tgz -C /var/lib trademaster && find /var/backups -name "trademaster-*.tgz" -mtime +14 -delete' > /etc/cron.d/trademaster-backup

echo ">> auto deploy: check GitHub every 2 minutes, update + restart only when new code was pushed"
echo '*/2 * * * * root flock -n /run/tm-update.lock bash /opt/trademaster/deploy/update.sh >> /var/log/trademaster-update.log 2>&1' > /etc/cron.d/trademaster-update

systemctl daemon-reload
systemctl enable --now trademaster caddy
systemctl restart trademaster
/usr/local/bin/trademaster-https
echo ">> DONE: $(cat /etc/trademaster.url)"
