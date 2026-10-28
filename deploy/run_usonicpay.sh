#!/bin/sh
# Wrapper invoked by systemd for USonicPay.
#
# Deliberately does NOT export the treasury key as an environment
# variable. An exported secret lands in /proc/<pid>/environ, which is
# world-readable on the host and shows up in `ps eww`, `systemctl show`,
# and crash dumps. The path is exported instead, which is not a secret.
#
# Sonic is EVM, so the key is a secp256k1 private key and the signer
# reads the 0600 file directly.
set -e

SECRETS_DIR="/opt/usonicpay/secrets"
KEY="$SECRETS_DIR/treasury.key"

if [ -r "$KEY" ]; then
    export USONICPAY_TREASURY_KEY="$KEY"
    # The signer refuses a key file it cannot read, so a wrong
    # permission here becomes a clear failure at start rather than a
    # mysterious one at first payment.
    if [ ! -r "$KEY" ]; then
        echo "treasury.key exists but is not readable by this user" >&2
        exit 1
    fi
else
    # Read-only mode. The service still serves balances and health; it
    # just cannot send. Better than refusing to boot, because the read
    # paths are useful before the treasury is funded.
    echo "no treasury key at $KEY; starting read-only" >&2
    unset USONICPAY_TREASURY_KEY
fi

exec /opt/usonicpay/venv/bin/python /opt/usonicpay/main.py
