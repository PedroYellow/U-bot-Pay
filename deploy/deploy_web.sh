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

# ── stray backup files ────────────────────────────────────────────────────
# Audit 2026-10-27 (L-3): eight backup files (.bak-*, .before-fix3,
# .pre-fix-agent) were sitting in the webroot and being served with HTTP 200.
# index.html.bak-* also carried a second-order XSS in the bridge error
# handler that the live index.html had already fixed.
#
# The nginx deny rule stops them being served, but they still pile up on
# disk and they are one config change away from being served again. So the
# deploy removes them and the verification below fails if any survive.
#
# Backups belong OUTSIDE the webroot. ARCHIVE_DIR, if set, receives a copy
# first; nothing is deleted without a copy unless ARCHIVE_DIR is empty.
ARCHIVE_DIR="${UBOT_WEB_ARCHIVE_DIR:-/root/audit/web-archive}"
stale=()
while IFS= read -r f; do
  [ -n "$f" ] && stale+=("$f")
done < <(find "$DST" -maxdepth 1 -type f \
  \( -name '*.bak-*' -o -name '*.before-fix*' -o -name '*.pre-fix*' \
     -o -name '*.post-fix*' -o -name '*.orig' -o -name '*.tmp' -o -name '*.swp' \) \
  2>/dev/null || true)

if [ "${#stale[@]}" -gt 0 ]; then
  if [ -n "$ARCHIVE_DIR" ] && mkdir -p "$ARCHIVE_DIR" 2>/dev/null; then
    for f in "${stale[@]}"; do
      cp -p "$f" "$ARCHIVE_DIR/$(basename "$f").$(date -u +%Y%m%dT%H%M%SZ)" 2>/dev/null || true
    done
    echo "archived ${#stale[@]} stale backup file(s) to $ARCHIVE_DIR"
  fi
  rm -f "${stale[@]}"
  echo "removed ${#stale[@]} stale backup file(s) from $DST"
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

# A stray backup file in the webroot is a deploy failure, not a warning.
leftover=$(find "$DST" -maxdepth 1 -type f \
  \( -name '*.bak-*' -o -name '*.before-fix*' -o -name '*.pre-fix*' \
     -o -name '*.post-fix*' -o -name '*.orig' -o -name '*.tmp' -o -name '*.swp' \) \
  2>/dev/null | wc -l)
if [ "$leftover" -ne 0 ]; then
  echo "VERIFY: $leftover backup file(s) still in $DST" >&2
  failed=1
fi

[ "$failed" -eq 0 ] || { echo "deploy incomplete" >&2; exit 1; }

deployed=$(( ${#ASSETS[@]} - ${#missing[@]} ))
echo "deployed $deployed/${#ASSETS[@]} assets to $DST at $(date -u +%FT%TZ)"