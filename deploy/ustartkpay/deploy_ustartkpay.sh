#!/usr/bin/env bash
# Deploy U-StarkPay — the Starknet whitelabel.
#
# Same shape as the other whitelabels: its own directory, its own
# venv, its own database, its own port, its own secrets, its own
# systemd unit. It shares a host with the other deployments and shares
# no process, no database, no port and no secret with them.
#
# Starknet is not EVM, so this deployment does NOT share an
# interpreter with the web3 deployments: starknet_py and web3 pin
# eth-abi differently, and the audit already showed what installing
# one into the other's venv does - it silently downgrades a shared
# dependency and breaks payments.
#
# The base is the Sonic deployment (the EVM one with the richest
# feature set), with:
#   - the Starknet engine in chains/
#   - the registry taught about the `starknet` kind
#   - the Starknet profile
#   - the Starknet prefix throughout
#
# Usage: deploy_ustartkpay.sh [--port 8095]
set -euo pipefail

DEPLOY_DIR="/opt/ustartkpay"
PORT="${2:-8095}"
BASE="/opt/usonicpay"
VENV="$DEPLOY_DIR/venv"
PREFIX="USTART"
SECRETS="$DEPLOY_DIR/secrets"

echo "=== the Starknet deployment ==="
echo "  directory : $DEPLOY_DIR"
echo "  port      : $PORT"
echo "  prefix    : $PREFIX"
echo "  base      : $BASE"

# ── 1. the directory and the app ───────────────────────────────────
echo
echo "=== the tree ==="
if [ ! -d "$DEPLOY_DIR" ]; then
    mkdir -p "$DEPLOY_DIR"
fi
# The app files, from the base deployment. The chains/ package comes
# with it - that is where the engines live.
for f in api.py brain.py bridge.py chain.py config.py core.py dataagent.py \
         energy.py main.py push.py tg.py web3_stub.py x402_client.py; do
    if [ -f "$BASE/$f" ]; then
        cp "$BASE/$f" "$DEPLOY_DIR/$f"
    fi
done
if [ -d "$BASE/chains" ]; then
    rm -rf "$DEPLOY_DIR/chains"
    cp -r "$BASE/chains" "$DEPLOY_DIR/chains"
    rm -rf "$DEPLOY_DIR/chains/__pycache__"
fi
mkdir -p "$DEPLOY_DIR/profiles" "$SECRETS" "$DEPLOY_DIR/data" "$DEPLOY_DIR/deploy"
cp "$BASE/requirements.txt" "$DEPLOY_DIR/" 2>/dev/null || true
echo "  the app is copied from $BASE"

# ── 2. the venv ────────────────────────────────────────────────────
echo
echo "=== the venv ==="
if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip
fi
# starknet_py 0.30.0. On 0.25.1 the v3 deploy failed at the RPC with
# "Field validation for 'L1DataGas' failed on the 'required' tag" -
# the transaction was missing a field a v3 transaction requires.
echo "  installing the dependencies"
"$VENV/bin/pip" install -q -r "$DEPLOY_DIR/requirements.txt" 2>&1 | tail -2 || true
"$VENV/bin/pip" install -q "starknet-py==0.30.0" 2>&1 | tail -2 || true
"$VENV/bin/pip" show starknet-py 2>/dev/null | head -2

# ── 3. the prefix everywhere ───────────────────────────────────────
echo
echo "=== the prefix ==="
# The prefix is set in the env file, and config.py reads it from
# DEPLOY_PREFIX. The other deployments' prefixes must not leak in.
for f in api.py brain.py bridge.py chain.py core.py dataagent.py \
         energy.py main.py push.py tg.py config.py; do
    [ -f "$DEPLOY_DIR/$f" ] || continue
    sed -i "s/\bUSONIC_/${PREFIX}_/g; s/\bUSONICPAY\b/${PREFIX}PAY/g" \
        "$DEPLOY_DIR/$f"
done
echo "  the USONIC prefix is now $PREFIX"

# ── 4. the engine and the registry ────────────────────────────────
echo
echo "=== the Starknet engine ==="
python3 /tmp/integrate_starknet_registry.py \
    "$DEPLOY_DIR/chains/registry.py" \
    /opt/ustartkpay_test/chains_starknet_engine.py 2>/dev/null || \
python3 "$DEPLOY_DIR/deploy/integrate_starknet_registry.py" \
    "$DEPLOY_DIR/chains/registry.py" \
    /opt/ustartkpay_test/chains_starknet_engine.py
"$VENV/bin/python" -m py_compile "$DEPLOY_DIR/chains/registry.py" \
    "$DEPLOY_DIR/chains/starknet.py" && echo "  it compiles"

# ── 5. the profile ────────────────────────────────────────────────
echo
echo "=== the profile ==="
cp /tmp/ustartkpay.yaml "$DEPLOY_DIR/profiles/ustartkpay.yaml" 2>/dev/null || true
grep -E "^kind:|^name:|^  chain_id:|^  stable_address:" \
    "$DEPLOY_DIR/profiles/ustartkpay.yaml" | sed 's/^/  /'

# ── 6. the env ────────────────────────────────────────────────────
echo
echo "=== the env ==="
if [ ! -f "$DEPLOY_DIR/.env" ]; then
    echo "  (there is no .env to copy - write one)"
fi
chmod 600 "$DEPLOY_DIR/.env" 2>/dev/null || true

# ── 7. the wrapper and the unit ───────────────────────────────────
echo
echo "=== the service ==="
cat > "$DEPLOY_DIR/deploy/run_ustartkpay.sh" <<'WRAPPER'
#!/bin/sh
# Wrapper invoked by systemd for U-StarkPay.
#
# Deliberately does NOT export the account key. An exported secret
# lands in /proc/<pid>/environ, which is world-readable on the host,
# and shows up in `ps eww`, `systemctl show` and crash dumps. The PATH
# is exported instead, which is not a secret.
#
# On Starknet the key belongs to an ACCOUNT CONTRACT, not to an
# address: the address is derived from the key, so the key is the
# whole thing.
set -e

SECRETS_DIR="/opt/ustartkpay/secrets"
KEY="$SECRETS_DIR/account.json"

if [ -r "$KEY" ]; then
    export USTARTPAY_ACCOUNT_KEY="$KEY"
else
    # Read-only mode. The service still serves balances and health; it
    # just cannot send. Better than refusing to boot, because the read
    # paths are useful before the treasury is funded.
    echo "no account key at $KEY; starting read-only" >&2
    unset USTARTPAY_ACCOUNT_KEY
fi

exec /opt/ustartkpay/venv/bin/python /opt/ustartkpay/main.py
WRAPPER
chmod +x "$DEPLOY_DIR/deploy/run_ustartkpay.sh"

cat > /etc/systemd/system/ustartkpay.service <<'UNIT'
[Unit]
# U-StarkPay — Starknet-side whitelabel of U-bot Pay.
#
# Same single-process shape (FastAPI + Telegram bot polling), pointing
# at /opt/ustartkpay, with its own venv, its own database, its own
# port and its own secrets. It shares a host with the other
# deployments but does not share a process, a database, a port, or a
# secret.
#
# Starknet is not EVM, so this service does NOT share an interpreter
# with the web3 deployments. starknet_py and web3 pin eth-abi
# differently, and installing one into the other's venv silently
# downgrades a shared dependency and breaks payments - which is
# exactly what happened when tronpy was installed into ubotpay's.
Description=U-StarkPay (Starknet + Telegram)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/ustartkpay
EnvironmentFile=/opt/ustartkpay/.env
ExecStart=/opt/ustartkpay/deploy/run_ustartkpay.sh
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
echo "  the unit is installed"

echo
echo "=== next ==="
echo "  write /opt/ustartkpay/.env, then:"
echo "    systemctl enable --now ustartkpay"
echo "    curl http://127.0.0.1:$PORT/dashboard"
