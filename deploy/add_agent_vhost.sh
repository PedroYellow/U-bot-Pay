#!/bin/bash
# Add an agent.* server block to a whitelabel vhost.
#
# The whitelabel pages reference agent.<domain> in their CSP and their
# markup (the DataAgent widget), and the DNS already points at this
# machine - but there was no server block for it, so the hostname
# resolved and then 404'd. The allowlist already permitted the agent
# origin, so the browser was ready for a server that did not exist.
#
# The block mirrors ubotpay's: the static agent page, the DataAgent
# backend on 8091, and the wallet-connect route to the main backend.
set -e

DOMAIN="$1"
NAME="$2"
VHOST="/etc/nginx/sites-available/${NAME}"

if [ -z "$DOMAIN" ] || [ -z "$NAME" ]; then
  echo "usage: $0 <domain> <vhost-name>"; exit 2
fi

# ── 0. the port-80 challenge block ──────────────────────────────────────
# certbot's webroot authenticator fetches
# http://agent.<domain>/.well-known/acme-challenge/<token> over plain
# HTTP. The vhost's port-80 block only listed the apex, www and api, so
# the request fell through to the default server and 404'd - which is
# what certbot reported. Add the host to that block first.
mkdir -p "/var/www/${NAME}"
if ! grep -q "server_name.*agent\.${DOMAIN}" "$VHOST"; then
  echo "--- adding agent.${DOMAIN} to the port-80 block ---"
  python3 /tmp/add_agent_port80.py "$VHOST" "$DOMAIN"
  nginx -t 2>&1 | grep -q successful && nginx -s reload && sleep 2
fi

# ── 1. certificate ──────────────────────────────────────────────────────
# The existing certs cover www.<domain> only, so agent.<domain> needs
# its own. The webroot is already served on port 80 by the vhost.
if [ ! -d "/etc/letsencrypt/live/agent.${DOMAIN}" ]; then
  echo "--- requesting cert for agent.${DOMAIN} ---"
  certbot certonly --webroot -w "/var/www/${NAME}" \
    -d "agent.${DOMAIN}" --non-interactive --agree-tos \
    --email pedro@yellow.com || {
      echo "certbot failed - aborting"; exit 1;
    }
fi

# ── 2. the server block ─────────────────────────────────────────────────
if grep -q "server_name agent.${DOMAIN};" "$VHOST"; then
  echo "agent block already present in $VHOST"
else
  echo "--- adding the agent server block to $VHOST ---"
  python3 - "$VHOST" "$DOMAIN" "$NAME" <<'PYEOF'
import sys
vhost, domain, name = sys.argv[1], sys.argv[2], sys.argv[3]

block = f"""
server {{
    server_name agent.{domain};
    server_tokens off;
    root /var/www/{name}/agent;
    types {{
        text/html                 html htm shtml;
        text/css                 css;
        application/javascript    js;
        application/json          json;
        image/png                 png;
        image/svg+xml             svg svgz;
        application/manifest+json webmanifest;
    }}
    index index.html;

    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header Content-Security-Policy "default-src 'self'; img-src 'self' data:; script-src 'self' 'unsafe-inline' https://unpkg.com; style-src 'self' 'unsafe-inline'; connect-src 'self' https://{domain} https://api.{domain}; frame-ancestors 'none'; base-uri 'self'; worker-src 'self' blob:;" always;
    add_header Access-Control-Allow-Origin ${name}_cors_origin always;
    add_header Access-Control-Allow-Methods "GET, POST, OPTIONS" always;
    add_header Access-Control-Allow-Headers "Content-Type, Authorization" always;

    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/agent.{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/agent.{domain}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # ── DataAgent backend (x402) ──
    location = /report {{ proxy_pass http://127.0.0.1:8091/report; proxy_set_header Host $host; proxy_read_timeout 120; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header X-Real-IP $remote_addr; }}
    location = /health  {{ proxy_pass http://127.0.0.1:8091/health; proxy_set_header Host $host; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header X-Real-IP $remote_addr; }}

    # ── wallet-connect from the main backend ──
    location /auth/   {{ proxy_pass http://127.0.0.1:8090/auth/; proxy_set_header Host $host; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header X-Real-IP $remote_addr; }}

    # ── Static web ──
    location = / {{ try_files /index.html /index.html; add_header Cache-Control "no-cache, must-revalidate"; }}
    location / {{ try_files $uri $uri.html $uri/ /index.html; add_header Cache-Control "no-cache, must-revalidate"; }}
}}
"""

s = open(vhost).read()
# insert before the port-80 block, which is the last one in the file
marker = "\n# ── Port 80"
if marker in s:
    i = s.index(marker)
    s = s[:i] + block + s[i:]
else:
    s = s.rstrip() + "\n" + block
open(vhost, "w").write(s)
print(f"agent.{domain} block added")
PYEOF
fi

# ── 3. the static agent page ────────────────────────────────────────────
if [ ! -f "/var/www/${NAME}/agent/index.html" ]; then
  echo "--- copying the agent page into ${NAME} ---"
  mkdir -p "/var/www/${NAME}/agent"
  cp /var/www/ubotpay/agent/index.html "/var/www/${NAME}/agent/index.html"
  # point it at this domain's API and agent host
  sed -i "s#https://api\.ubotpay\.com#https://api.${DOMAIN}#g" \
         "/var/www/${NAME}/agent/index.html"
  sed -i "s#https://agent\.ubotpay\.com#https://agent.${DOMAIN}#g" \
         "/var/www/${NAME}/agent/index.html"
  sed -i "s#https://ubotpay\.com#https://${DOMAIN}#g" \
         "/var/www/${NAME}/agent/index.html"
fi

# ── 4. validate and reload ──────────────────────────────────────────────
echo "--- nginx config test ---"
if ! nginx -t 2>&1 | grep -q "successful"; then
  echo "CONFIG INVALID - not reloading"
  nginx -t 2>&1 | tail -5
  exit 1
fi
nginx -s reload
sleep 2
echo "reloaded"
