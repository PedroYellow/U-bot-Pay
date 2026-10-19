#!/usr/bin/env bash
# Deploy web/ to /var/www/ubotpay. NO secrets are injected into the browser bundle.
# Run ON the VPS: bash deploy_web.sh <src_dir>
set -euo pipefail
SRC="${1:-/opt/ubotpay/web}"; DST=/var/www/ubotpay

# Copy HTML/JS/CSS as-is. The browser bundle never contains API keys or session tokens.
for f in index.html agent.html; do
  install -m 0644 "$SRC/$f" "$DST/$f"
done
install -m 0644 "$SRC/ubot.css" "$DST/ubot.css"
install -m 0644 "$SRC/i18n.js" "$DST/i18n.js"
[ -d "$SRC/i18n" ] && mkdir -p "$DST/i18n" && install -m 0644 "$SRC"/i18n/* "$DST/i18n/"

# Sanity: no API keys, no long random tokens, no x-api-key references in browser assets.
if grep -qE "sk-(ant|nous|or)[a-z0-9_-]{8,}|x-api-key" "$DST/index.html" "$DST/agent.html"; then
  echo "SECURITY: browser bundle contains a secret-like token. Aborting." >&2
  exit 1
fi
echo "deployed $(date -u +%FT%TZ)"
