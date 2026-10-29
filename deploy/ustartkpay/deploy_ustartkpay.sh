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
mkdir -p "$DEPLOY_DIR/profiles" "$SECRETS" "$DEPLOY_DIR/data" "$DEPLOY_DIR/deploy" \
         "$DEPLOY_DIR/deploy/ustartkpay"
# The base's requirements come FIRST, and the Starknet ones overwrite
# them. The order matters: the base is the Sonic deployment, and its
# requirements.txt lists web3 and eth-account, which is exactly what
# cannot be installed beside starknet-py.
#
# It was the other way round once, and the deploy failed with
# ResolutionImpossible on every run - because the base's file was
# copied over the Starknet one right after the Starknet one was put
# in place.
cp "$BASE/requirements.txt" "$DEPLOY_DIR/" 2>/dev/null || true

# The Starknet requirements, and the EVM stub. Both come from the
# repository's deploy/ustartkpay/ directory, and both are needed
# BEFORE the venv is built - the requirements decide what goes in it,
# and the stub is what dataagent.py imports instead of web3.
#
# The stub is also copied to the deployment root, because dataagent.py
# imports it as a top-level module.
for f in requirements.txt evm_stub.py; do
    SRC="/opt/ustartkpay/deploy/ustartkpay/$f"
    if [ ! -f "$SRC" ]; then
        continue
    fi
    # cp fails when the source and the destination are the same file,
    # and `set -e` turns that into a dead deploy. It happens on the
    # second run, because the first run already put the file where it
    # belongs.
    if [ "$SRC" != "$DEPLOY_DIR/deploy/ustartkpay/$f" ]; then
        cp "$SRC" "$DEPLOY_DIR/deploy/ustartkpay/$f"
    fi
    if [ "$SRC" != "$DEPLOY_DIR/$f" ]; then
        cp "$SRC" "$DEPLOY_DIR/$f"
    fi
done
echo "  the Starknet requirements and the EVM stub are in place"
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
# The requirements are the STARKNET ones - no web3, no eth-account.
#
# starknet-py 0.30.0 wants eth-keyfile<1.0.0,>=0.8.1 and
# eth-account 0.14.0 wants eth-keyfile>=0.10. Those are incompatible,
# and installed together the resolver picks eth-keyfile 0.9.1, which
# breaks eth-account:
#
#     eth-account 0.14.0 requires eth-keyfile>=0.10, but you have
#     eth-keyfile 0.9.1 which is incompatible.
#
# and pip exits 120. Two chain SDKs cannot share an interpreter.
#
# So the Starknet requirements file has no EVM SDKs in it at all, and
# the EVM helpers come from evm_stub.py - which is what api.py already
# did. See deploy/ustartkpay/requirements.txt for the full reasoning.
#
# starknet-py is in that file, so it is ONE install command and the
# resolver sees every constraint at once.
if [ -f "$DEPLOY_DIR/deploy/ustartkpay/requirements.txt" ]; then
    REQS="$DEPLOY_DIR/deploy/ustartkpay/requirements.txt"
else
    REQS="$DEPLOY_DIR/requirements.txt"
fi
echo "  the requirements: $REQS"
"$VENV/bin/pip" install -q -r "$REQS" 2>&1 | tail -3 || true
# `pip show` returns 120 when there is a dependency conflict WARNING,
# even though it printed the version. With `set -e` that killed the
# deploy at the last step - after a venv that was perfectly fine. So
# the exit code is explicitly ignored here and the real check is the
# `pip check` below, which reads the ACTUAL state.
"$VENV/bin/pip" show starknet-py 2>/dev/null | head -2 || true
# the resolution is checked, not assumed
"$VENV/bin/python" - <<'CHECK' || exit 1
import subprocess, sys
r = subprocess.run([sys.executable, "-m", "pip", "check"],
                   capture_output=True, text=True)
# `pip check` writes its result to STDERR, not stdout. Reading only
# stdout made an empty string look like a clean check - and the
# `|| exit 1` then killed the deploy at the last step, after a venv
# that was perfectly fine.
out = (r.stdout or "").strip() or (r.stderr or "").strip()
if r.returncode != 0 and "No broken requirements found" not in out:
    print("  pip check:", out)
    sys.exit(1)
if out and "No broken requirements" not in out:
    print("  pip check:", out)
    sys.exit(1)
print("  pip check: no conflicts")
CHECK

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

# ── 3b. the EVM imports in dataagent.py ────────────────────────────
# dataagent.py is carried over from U-bot Pay and imports web3 and
# eth_account at module level. This deployment has neither - and cannot
# have them, because starknet-py and eth-account need incompatible
# versions of eth-keyfile.
#
# So the imports become the stub. The only one that is used is
# Web3.to_checksum_address, and the stub's version of it is correct
# for a non-EVM chain.
echo
echo "=== the EVM imports ==="
if [ -f "$DEPLOY_DIR/dataagent.py" ]; then
    if grep -q "^from web3 import Web3" "$DEPLOY_DIR/dataagent.py"; then
        python3 "$DEPLOY_DIR/deploy/ustartkpay/fix_starknet_dataagent_imports.py" \
            "$DEPLOY_DIR/dataagent.py"
    else
        echo "  it already uses the stub"
    fi
    grep -n "from evm_stub\|from web3" "$DEPLOY_DIR/dataagent.py" | head -3
fi

# ── 4. the engine and the registry ────────────────────────────────
echo
echo "=== the Starknet engine ==="
# The engine comes from the REPOSITORY's copy, not from
# /opt/ustartkpay_test - that is a test directory, it is not kept in
# sync, and it is where a stale engine was living.
#
# And it has to land at chains/starknet.py, because that is the name
# the registry imports:
#
#     from .starknet import StarknetEngine
#
# The bug this fixes: the deployment ended up with TWO copies of the
# engine - chains/starknet.py (the OLD one, the one that is imported)
# and chains_starknet_engine.py (the fixed one, imported by nothing).
# So three real fixes were deployed and none of them was live.
#
# After the copy, both names are made identical, so there is no
# second copy to drift.
ENGINE_SRC="/opt/ustartkpay/deploy/ustartkpay/chains_starknet_engine.py"
ENGINE_DST="$DEPLOY_DIR/chains/starknet.py"

if [ ! -f "$ENGINE_SRC" ]; then
    echo "  the engine source is missing: $ENGINE_SRC"
    echo "  (it comes from the repository - deploy/ustartkpay/chains_starknet_engine.py)"
    exit 1
fi

cp "$ENGINE_SRC" "$ENGINE_DST"
# The second name is a COPY, and a copy is what caused this whole
# class of bug: the deployment had chains/starknet.py (the old one,
# the one the registry imports) and chains_starknet_engine.py (the
# fixed one, imported by nothing). Three fixes were deployed and none
# of them was live.
#
# So the second name is not a separate copy that can drift. It is the
# SAME file - a symlink - so there is only one engine in the
# deployment and the check can prove it.
rm -f "$DEPLOY_DIR/chains_starknet_engine.py"
ln -s chains/starknet.py "$DEPLOY_DIR/chains_starknet_engine.py"

python3 "$DEPLOY_DIR/deploy/integrate_starknet_registry.py" \
    "$DEPLOY_DIR/chains/registry.py" \
    "$ENGINE_SRC"
"$VENV/bin/python" -m py_compile "$DEPLOY_DIR/chains/registry.py" \
    "$DEPLOY_DIR/chains/starknet.py" && echo "  it compiles"

# the check that the file the service imports is the fixed one
"$VENV/bin/python" "$DEPLOY_DIR/check_starknet_engine_copy.py" "$DEPLOY_DIR" \
    2>/dev/null | tail -3 || true

# ── 5. the profile ────────────────────────────────────────────────
echo
echo "=== the profile ==="
# From the REPOSITORY's copy, not from /tmp. The /tmp one was a
# leftover from an earlier run, it is not kept in sync, and it is why
# the deployed profile still said key_file: account.json - the
# SEPOLIA key - while the code expected the mainnet one.
PROFILE_SRC="/opt/ustartkpay/deploy/ustartkpay/profiles/ustartkpay.yaml"
if [ -f "$PROFILE_SRC" ]; then
    cp "$PROFILE_SRC" "$DEPLOY_DIR/profiles/ustartkpay.yaml"
else
    echo "  the profile source is missing: $PROFILE_SRC"
    exit 1
fi
grep -E "^kind:|^name:|^  chain_id:|^  stable_address:|^  key_file:|^  network:" \
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
# The MAINNET key. The sepolia one is account.json, and it belongs to
# another network - the engine now refuses a key whose network does not
# match the deployment's, so pointing at it would make the deployment
# read-only.
#
# This is the same file the profile names in key_file. Both must agree,
# because the env var is what a person reads and the profile is what
# the code uses.
KEY="$SECRETS_DIR/mainnet_account.json"

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
