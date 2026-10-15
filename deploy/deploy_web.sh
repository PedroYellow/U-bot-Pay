#!/usr/bin/env bash
# Deploy web/ to /var/www/ubotpay injecting secrets from /opt/ubotpay/.env. Run ON the VPS: bash deploy_web.sh <src_dir>
set -euo pipefail
SRC="${1:-/opt/ubotpay/web}"; DST=/var/www/ubotpay
KEY=$(grep -E '^UBOT_API_KEY=' /opt/ubotpay/.env | cut -d= -f2-)
AGENT=${ELEVEN_AGENT_ID:-agent_3601m4yz1xhgffmb1mb4j0sab89a}
for f in index.html agent.html; do
  sed -e "s/__UBOT_API_KEY__/$KEY/g" -e "s/__AGENT_ID__/$AGENT/g" "$SRC/$f" > "$DST/$f"
done
cp "$SRC/ubot.css" "$DST/ubot.css"; cp "$SRC/i18n.js" "$DST/i18n.js"
[ -d "$SRC/i18n" ] && mkdir -p "$DST/i18n" && cp "$SRC"/i18n/* "$DST/i18n/"
grep -q "__UBOT_API_KEY__\|__AGENT_ID__" "$DST/index.html" "$DST/agent.html" && { echo "placeholder left!"; exit 1; }
echo "deployed $(date -u +%FT%TZ)"
