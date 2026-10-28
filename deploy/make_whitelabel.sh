#!/usr/bin/env bash
# Build a U-bot Pay whitelabel deployment: static site + nginx vhost.
#
# Usage:  bash deploy/make_whitelabel.sh <brand> <domain> [webroot] [vhost]
#
#   brand     short name, used for the service-worker cache name and the
#             rate-limit zone names. Lowercase, no dots. e.g. "usonicpay"
#   domain    the public domain. e.g. "usonicpay.com"
#   webroot   default /var/www/<brand>
#   vhost     default /etc/nginx/sites-available/<brand>
#
# What it does:
#   1. copies web/ into the webroot
#   2. rewrites the API host, the agent host and the brand string
#   3. gives the service worker a per-deployment cache name, so two
#      whitelabels do not fight over one cache in the browser
#   4. renders the nginx vhost from the ubotpay one: new server_name, new
#      root, its own rate-limit zones, its own CORS origin, its own cert
#      paths, and a port-80 block that serves the ACME challenge so
#      certbot can renew
#
# It does NOT touch DNS. Set the A records to the server IP first, then
# run this, then enable the vhost, then issue the certificate.
set -euo pipefail

BRAND="${1:?usage: make_whitelabel.sh <brand> <domain> [webroot] [vhost]}"
DOMAIN="${2:?usage: make_whitelabel.sh <brand> <domain> [webroot] [vhost]}"
WEBROOT="${3:-/var/www/$BRAND}"
VHOST="${4:-/etc/nginx/sites-available/$BRAND}"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC_WEB="$REPO/web"
SRC_VHOST="${UBOT_NGINX_VHOST:-$REPO/deploy/nginx.conf}"

if [ ! -d "$SRC_WEB" ]; then
  echo "no web/ source at $SRC_WEB" >&2
  exit 1
fi
if [ ! -f "$SRC_VHOST" ]; then
  echo "no nginx source at $SRC_VHOST" >&2
  exit 1
fi

echo "── $BRAND / $DOMAIN ──────────────────────────────"

# ── 1. static site ───────────────────────────────────────────────────────
mkdir -p "$WEBROOT"
for f in index.html agent.html analytics.html i18n.js sw.js manifest.json \
         security.txt ubot.css favicon-32x32.png icon-192.png icon-512.png \
         apple-touch-icon.png; do
  [ -f "$SRC_WEB/$f" ] && install -m 0644 "$SRC_WEB/$f" "$WEBROOT/$f"
done

# ── 2 + 3. rewrite the hosts and the brand ───────────────────────────────
# Order matters: api. and agent. are longer than the bare host, so they
# must be replaced first or they would be mangled by the bare rule.
python3 - "$WEBROOT" "$DOMAIN" "$BRAND" <<'PYEOF'
import os, re, sys
webroot, domain, brand = sys.argv[1], sys.argv[2], sys.argv[3]
files = ["index.html", "agent.html", "analytics.html", "i18n.js", "sw.js",
         "manifest.json", "security.txt"]
for fname in files:
    p = os.path.join(webroot, fname)
    if not os.path.exists(p):
        continue
    s = open(p, encoding="utf-8").read()
    s = s.replace("https://api.ubotpay.com", f"https://api.{domain}")
    s = s.replace("https://agent.ubotpay.com", f"https://agent.{domain}")
    s = s.replace("https://www.ubotpay.com", f"https://www.{domain}")
    s = s.replace("https://ubotpay.com", f"https://{domain}")
    s = s.replace("ubotpay.com", domain)
    # the service-worker cache name must be per deployment
    s = s.replace("ubotpay", brand)
    open(p, "w", encoding="utf-8").write(s)
print(f"  static site rewritten for {domain}")
PYEOF

# ── 4. nginx vhost ───────────────────────────────────────────────────────
python3 - "$SRC_VHOST" "$VHOST" "$DOMAIN" "$BRAND" "$WEBROOT" <<'PYEOF'
import re, sys
src_path, out_path, domain, brand, webroot = sys.argv[1:6]
src = open(src_path).read()

rest = src[src.index("server {"):]
blocks = re.split(r'\n(?=server \{)', rest)
main = next((b for b in blocks if "server_name ubotpay.com" in b), None)
if main is None:
    sys.exit("could not find the main server block in " + src_path)

b = main
b = re.sub(r'server_name\s+[^;]+;',
           f'server_name {domain} www.{domain} api.{domain};', b, count=1)
b = re.sub(r'root\s+/var/www/[a-z]+;', f'root {webroot};', b, count=1)
b = re.sub(r'ssl_certificate\s+\S+;',
           f'ssl_certificate /etc/letsencrypt/live/www.{domain}/fullchain.pem;', b)
b = re.sub(r'ssl_certificate_key\s+\S+;',
           f'ssl_certificate_key /etc/letsencrypt/live/www.{domain}/privkey.pem;', b)
b = b.replace("https://api.ubotpay.com", f"https://api.{domain}")
b = b.replace("https://agent.ubotpay.com", f"https://agent.{domain}")
b = b.replace("https://ubotpay.com", f"https://{domain}")
b = re.sub(r'location /pitch \{ alias /var/www/[a-z]+/pitch;',
           f'location /pitch {{ alias {webroot}/pitch;', b)

# ── rate-limit zones ─────────────────────────────────────────────────────
# The zone names are global in nginx, so this deployment needs its own.
# Derived from the domain's first label, minus a trailing "pay" only when
# what remains is still a sensible name: "usonicpay" -> "usonic",
# "ustartkpay" -> "ustartk".
label = domain.split(".")[0]
short = label
for suffix in ("pay", ".com"):
    if short.endswith(suffix) and len(short) - len(suffix) >= 4:
        short = short[: -len(suffix)]
        break
short = short.replace("-", "").replace(".", "")
decl = (f"limit_req_zone $binary_remote_addr zone={short}turn:10m rate=20r/m;\n"
        f"limit_req_zone $binary_remote_addr zone={short}quote:10m rate=20r/m;\n")
b = re.sub(r'^limit_req_zone .*\n', '', b, flags=re.M)
b = re.sub(r'limit_req\s+zone=\w+', f'limit_req zone={short}turn', b)
# the quote endpoint has its own bucket
b = re.sub(r'limit_req zone=%sturn burst=10 nodelay;' % short,
           f'limit_req zone={short}quote burst=10 nodelay;', b)

# ── ACME challenge + /.well-known/ ───────────────────────────────────────
# The ACME challenge must be fetchable over http, or certbot's webroot
# renewer cannot renew and the certificate silently expires. The
# /.well-known/ block needs an explicit root for the same reason.
acme = f"""    # Certbot webroot renewal: the ACME challenge must be fetchable over
    # http on the bare domain. Without this, renewal fails silently ~60
    # days after issue and the site ends up serving an expired cert.
    location ^~ /.well-known/acme-challenge/ {{
        root {webroot};
        default_type text/plain;
        try_files $uri =404;
    }}
"""
if "acme-challenge" not in b:
    anchor = "    location ^~ /.well-known/ {"
    if anchor in b:
        b = b.replace(anchor, acme + anchor, 1)
    else:
        a2 = "    location / {\n        try_files $uri $uri.html $uri/ /index.html;"
        b = b.replace(a2, acme + a2, 1)
b = re.sub(r'(location \^~ /\.well-known/ \{)\n(\s+)default_type',
           rf'\1\n\2root {webroot};\n\2default_type', b)

# ── CORS map ─────────────────────────────────────────────────────────────
# This is a file-scope directive, so it cannot live inside the server
# block; it goes in the scope header above it. The pattern is built from
# the domain's labels so "usonicpay.com" matches both
# https://usonicpay.com and https://agent.usonicpay.com.
label = domain.split(".")[0]
cors = f"""map $http_origin $cors_origin {{
    default "";
    ~^https://({label}|agent\\.{label})\\.com$ $http_origin;
}}

"""
header = f"""# U-bot Pay — {domain} whitelabel
# Same backend as ubotpay (127.0.0.1:8090), separate hostname, separate
# rate-limit buckets and its own CORS origin. Rate-limit zone names are
# global in nginx, so they must differ from the ubotpay ones.

{decl}
{cors}"""

# ── port 80 ──────────────────────────────────────────────────────────────
p80 = f"""
server {{
    # Port 80: serve the ACME challenge for certbot's webroot renewer and
    # redirect everything else to https. Without this the renewer cannot
    # complete a challenge and the certificate silently expires ~60 days
    # after issue.
    listen 80;
    server_name {domain} www.{domain} api.{domain};

    location ^~ /.well-known/acme-challenge/ {{
        root {webroot};
        default_type text/plain;
        try_files $uri =404;
    }}
    location / {{ return 301 https://$host$request_uri; }}
}}
"""

open(out_path, "w").write(header + b.rstrip() + "\n" + p80)
print(f"  vhost written to {out_path}")
PYEOF

echo "  done. Next:"
echo "    1. A records for $DOMAIN, www.$DOMAIN, api.$DOMAIN -> the server IP"
echo "    2. nginx -t && ln -s $VHOST /etc/nginx/sites-enabled/$BRAND && systemctl reload nginx"
echo "    3. certbot certonly --webroot -w $WEBROOT -d $DOMAIN -d www.$DOMAIN -d api.$DOMAIN \\"
echo "         --cert-name www.$DOMAIN --non-interactive --agree-tos --email security@ubotpay.com"
echo "    4. add the origin to UBOT_CORS_ORIGINS in the API .env and restart it"
