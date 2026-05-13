#!/usr/bin/env bash
# Idempotent setup script for AWS Lightsail Ubuntu 22.04.
# Run once as ubuntu user after cloning the repo to /opt/market-sentinel.
set -euo pipefail

APP_DIR="/opt/market-sentinel"
VENV="$APP_DIR/.venv"

echo "==> Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y python3.11 python3.11-venv python3-pip nginx nodejs npm

echo "==> Creating Python virtual environment"
python3.11 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Building React frontend"
cd "$APP_DIR/frontend"
npm install --silent
npm run build

echo "==> Creating data directories"
mkdir -p "$APP_DIR/data/ticks"

echo "==> Installing nginx config"
sudo cp "$APP_DIR/nginx.conf" /etc/nginx/sites-available/market-sentinel
sudo ln -sf /etc/nginx/sites-available/market-sentinel /etc/nginx/sites-enabled/market-sentinel
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

echo "==> Installing systemd service"
sudo cp "$APP_DIR/market-sentinel.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable market-sentinel

echo "==> Adding daily tick CSV purge cron (midnight)"
(crontab -l 2>/dev/null; echo "0 0 * * * find $APP_DIR/data/ticks -name '*.csv' -mtime +1 -delete") \
  | sort -u | crontab -

echo ""
echo "Setup complete."
echo "  1. Copy your .env file to $APP_DIR/.env"
echo "  2. Set mock_mode: false in config.yaml when ready for live data"
echo "  3. sudo systemctl start market-sentinel"
echo "  4. sudo journalctl -u market-sentinel -f   # tail logs"
