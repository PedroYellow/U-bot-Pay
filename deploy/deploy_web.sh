#!/usr/bin/env bash
# Deploy web/ to /var/www/ubotpay. NO secrets are injected into the browser bundle.
# Run ON the VPS: bash deploy_web.sh [src_dir] [dst_dir]
#
# dst_dir is overridable so the script can be exercised without touching
# /var/www, and so a staging deploy is possible.
#
# Audit 20 Oct round 9: this script only copied index.html, agent.html, ubot.css
# and i18n.js. sw.js, manifest.json, the PWA icons and security.txt were live in
# production only because somebody had uploaded them by hand at some point — the
# deploy path could not reproduce the site. Anything missing from the server fell
# back to the SPA catch-all, so /.well-known/security.txt returned index.html with
# a 200 and read as "we have one" to anyone checking.
#
# Now: an explicit manifest of everything the site serves, plus a verification pass
# that fails loudly if a file is missing from the destination afterwards.
set -euo pipefail
SRC="${1:-/opt/ubotpay/web}"; DST="${2:-/var/www/ubotpay}"

mkdir -p "$DST"

# The complete list of what the browser site is made of. Anything not here does
# not get deployed, which is deliberate — see the security check below.
ASSETS=(
  index.html
  agent.html
  analytics.html
  ubot.css
  i18n.js
  sw.js
  manifest.json
  security.txt
  favicon-32x32.png
  icon-192.png
  icon-512.png
  apple-touch-icon.png
)

missing=()
for f in "${ASSETS[@]}"; do
  if [ -f "$SRC/$f" ]; then
    install -m 0644 "$SRC/$f" "$DST/$f"
  else
    missing+=("$f")
  fi
done

if [ -d "$SRC/i18n" ]; then
  mkdir -p "$DST/i18n"
  install -m 0644 "$SRC"/i18n/* "$DST/i18n/"
fi

# Optional extras that may legitimately not exist in a source checkout.
for d in pitch; do
  [ -d "$SRC/$d" ] && { mkdir -p "$DST/$d"; cp -r "$SRC/$d/." "$DST/$d/"; }
done

# ── security check ─────────────────────────────────────────────────────────
# The browser bundle must never carry a server credential.
#
# x-api-key here is the *header name*, and the service worker legitimately
# references it (sw.js skips caching any request that carries one). Matching the
# bare string aborted a correct deploy. What actually matters is a key *value*
# being assigned or sent, so we look for that instead.
if grep -rqE "sk-(ant|nous|or)[a-zA-Z0-9_-]{8,}|[\"']x-api-key[\"']\s*[:=]\s*[\"'][^\"']{8,}" \
     "$DST/index.html" "$DST/agent.html" "$DST/analytics.html" "$DST/i18n.js" "$DST/sw.js" 2>/dev/null; then
  echo "SECURITY: browser bundle contains a secret-like token. Aborting." >&2
  grep -rnoE "sk-(ant|nous|or)[a-zA-Z0-9_-]{8,}|[\"']x-api-key[\"']\s*[:=]\s*[\"'][^\"']{8,}" \
       "$DST/index.html" "$DST/agent.html" "$DST/analytics.html" "$DST/i18n.js" "$DST/sw.js" | head >&2
  exit 1
fi

# ── verification ───────────────────────────────────────────────────────────
# A partial deploy that reports success is worse than a failed one. But a file
# missing from the *source* is a different situation: warn about it, deploy the
# rest, and don't fail the whole run for an optional asset.
for f in "${missing[@]:-}"; do
  [ -n "$f" ] && echo "WARNING: not present in $SRC, not deployed: $f" >&2
done

failed=0
for f in "${ASSETS[@]}"; do
  case " ${missing[*]:-} " in *" $f "*) continue ;; esac
  if [ ! -s "$DST/$f" ]; then
    echo "VERIFY: $f is missing or empty in $DST" >&2
    failed=1
  fi
done
[ "$failed" -eq 0 ] || { echo "deploy incomplete" >&2; exit 1; }

deployed=$(( ${#ASSETS[@]} - ${#missing[@]} ))
echo "deployed $deployed/${#ASSETS[@]} assets to $DST at $(date -u +%FT%TZ)"