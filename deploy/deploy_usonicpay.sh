#!/bin/bash
# Deploy USonicPay — the Sonic whitelabel.
#
# Sonic is EVM-compatible, so this service is the same shape as the
# others: its own directory, its own venv, its own database, its own
# port, its own secrets. It shares a host with ubotpay, usolpay and
# utronpay and shares nothing else.
#
# What this does:
#   1. creates /opt/usonicpay and its venv
#   2. writes the .env with Sonic-specific values
#   3. moves the treasury key into place, 0600
#   4. installs the systemd unit and the wrapper
#   5. starts it and checks that it answers
#
# Usage: deploy_usonicpay.sh [--key <path-to-treasury-key>]
set -e

DIR=/opt/usonicpay
PORT=8094
VENV=$DIR/venv
PROFILE=usonicpay
TREASURY_KEY_SRC=""

while [ $# -gt 0 ]; do
  case "$1" in
    --key) TREASURY_KEY_SRC="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

echo "── 1. directory and venv ──"
mkdir -p "$DIR" "$DIR/secrets" "$DIR/data"
if [ ! -x "$VENV/bin/python" ]; then
  echo "  creating the venv"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  # web3 only. Sonic is EVM, so this needs no solana-py and no tronpy.
  "$VENV/bin/pip" install -q -r "$DIR/requirements.txt" || {
    echo "  requirements install failed; falling back to web3 alone"
    "$VENV/bin/pip" install -q "web3>=7,<8" fastapi "uvicorn[standard]" httpx pyyaml
  }
fi
echo "  venv ready"

echo "── 2. the .env ──"
# Secrets are generated here rather than copied from another deployment:
# two deployments sharing a JWT secret would let a token from one be
# replayed against the other.
#
# But they are only generated ONCE. A deploy that regenerates them
# invalidates every issued JWT - the JWT secret signs the session
# tokens - and breaks the ElevenLabs voice webhook, which authenticates
# with the voice secret. An existing .env keeps its secrets; only a
# first deploy generates them.
#
# The same applies to the API key: the front-end and the DataAgent
# call this deployment with it, so regenerating it breaks them.
read_existing() {
  # print the value of a key from the existing .env, if there is one
  [ -f "$DIR/.env" ] || return 0
  sed -n "s/^$1=//p" "$DIR/.env" | head -1
}

JWT=$(read_existing USONIC_JWT_SECRET)
[ -n "$JWT" ] || JWT=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')

VOICE=$(read_existing USONIC_VOICE_SECRET)
[ -n "$VOICE" ] || VOICE=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')

APIKEY=$(read_existing USONIC_API_KEY)
[ -n "$APIKEY" ] || APIKEY=$(head -c 32 /dev/urandom | base64 | tr -d ' \n')

cat > "$DIR/.env" <<EOF
# USonicPay — Sonic-side whitelabel of U-bot Pay.
#
# Every value here is specific to this deployment. It shares no secret,
# no port and no database with ubotpay, usolpay or utronpay.

# The prefix every setting below is read under. config.py turns
# cfg("PORT") into USONIC_PORT. Without this line the service falls
# back to USOL_* and every setting silently resolves to its default.
DEPLOY_PREFIX=USONIC

USONIC_PROFILE=$PROFILE
USONIC_PORT=$PORT
USONIC_API_URL=http://127.0.0.1:$PORT
USONIC_DB=$DIR/data/usonicpay.db

# The treasury key is NOT here. Only its path is. The signer reads the
# 0600 file directly so the key never lands in /proc/<pid>/environ.
USONICPAY_TREASURY_KEY=$DIR/secrets/treasury.key

# The Sonic treasury address. The key controls it - proven by signing
# and recovering before the address was written here.
USONIC_WALLET_ADDRESS=0xCC57d2f8A941B3408f939E6fe949fb7C2b1F01Af

# The treasury's own address, where the fee goes.
USONIC_REVENUE_ADDRESS=0xCC57d2f8A941B3408f939E6fe949fb7C2b1F01Af

# Only the whitelabel's own origins. The main site is not allowed here.
USONIC_CORS_ORIGINS=https://usonicpay.com,https://www.usonicpay.com

# Fees and limits. Sonic gas is paid in S, which is cheap.
USONIC_FEE_PCT=0.5
USONIC_FEE_MIN_USDC=0.10
USONIC_DEFAULT_DAILY_LIMIT=500
USONIC_DEFAULT_DUAL_THRESHOLD=5

# Generated for this deployment, not copied from another one, and
# kept across deploys - see the note above.
USONIC_JWT_SECRET=$JWT
USONIC_VOICE_SECRET=$VOICE

# The server-to-server key. The service refuses to start without it.
USONIC_API_KEY=$APIKEY

USONIC_DAY_TZ_OFFSET=0
USONIC_SETTLING_MAX_AGE_S=900
USONIC_REAP_INTERVAL_S=120
USONIC_DOCS_PUBLIC=0
EOF
chmod 600 "$DIR/.env"
echo "  written, 0600"

echo "── 3. the treasury key ──"
if [ -n "$TREASURY_KEY_SRC" ]; then
  install -m 0600 "$TREASURY_KEY_SRC" "$DIR/secrets/treasury.key"
  echo "  installed from $TREASURY_KEY_SRC, 0600"
elif [ -f "$DIR/secrets/treasury.key" ]; then
  echo "  already present, left alone"
else
  echo "  no key given and none present; the service will start read-only"
fi

echo "── 4. the systemd unit and wrapper ──"
install -m 0644 /opt/usonicpay/deploy/usonicpay.service /etc/systemd/system/usonicpay.service
# The wrapper may already be in place from the copy; only install it when
# it is somewhere else, because install refuses to copy a file onto itself.
if [ "$(readlink -f "$DIR/deploy/run_usonicpay.sh")" != "$(readlink -f /opt/usonicpay/deploy/run_usonicpay.sh)" ]; then
  install -m 0755 /opt/usonicpay/deploy/run_usonicpay.sh "$DIR/deploy/run_usonicpay.sh"
fi
chmod 0755 "$DIR/deploy/run_usonicpay.sh"
systemctl daemon-reload
echo "  installed"

echo "── 5. start ──"
systemctl enable usonicpay >/dev/null 2>&1 || true
systemctl restart usonicpay
sleep 6
if systemctl is-active --quiet usonicpay; then
  echo "  usonicpay is active"
else
  echo "  usonicpay FAILED to start:"
  journalctl -u usonicpay --no-pager -n 25
  exit 1
fi

echo "── 6. does it answer? ──"
for path in /health /treasury /dashboard; do
  printf "  %-12s %s\n" "$path" \
    "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}${path}")"
done
echo
curl -s "http://127.0.0.1:${PORT}/treasury" | head -c 300
echo
