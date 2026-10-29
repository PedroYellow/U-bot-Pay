#!/usr/bin/env python3
"""Write the Starknet deployment's env file.

Every value is specific to this deployment. It shares no secret, no
port and no database with ubotpay, usolpay, usonicpay or utronpay.

The secrets are GENERATED, not copied. A copy would mean two
deployments share a JWT secret, and a token minted by one would be
accepted by the other.

The account key is NOT here. Only its path is. On Starknet the key
belongs to an ACCOUNT CONTRACT - the address is derived from it - so
the key is the whole thing, and it stays in a 0600 file that the
engine reads directly. An exported secret lands in
/proc/<pid>/environ, which is world-readable on the host, and shows up
in `ps eww`, `systemctl show` and crash dumps.

Usage: make_env_ustartkpay.py <env-path>
"""
import os
import secrets
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/opt/ustartkpay/.env"

PREFIX = "USTART"

# The Sepolia account, deployed and proven to sign. Its key is at
# /opt/ustartkpay_secrets/starknet_sepolia_account.json.
#
# The deployment's own account is on MAINNET, which is not funded yet,
# so the treasury address starts empty and the engine runs read-only.
# That is better than refusing to boot: the read paths are useful
# before the treasury is funded.
SEP_ADDR = "0x4af96d3945b91f77f2f36abfd45291ec5e0d7d753709ee2a5484a34f05ce259"

env = f"""# U-StarkPay — Starknet-side whitelabel of U-bot Pay.
#
# Every value here is specific to this deployment. It shares no secret,
# no port and no database with ubotpay, usolpay, usonicpay or utronpay.
#
# Starknet is not EVM. It is a Cairo chain with account abstraction:
# an account is a CONTRACT, the address is derived from
# (public key, salt, class hash), and the contract has to be deployed
# at it before it can sign anything.

# The prefix every setting in this file is read under. config.py
# turns cfg("PORT") into {PREFIX}_PORT.
DEPLOY_PREFIX={PREFIX}

{PREFIX}_PROFILE=ustartkpay
{PREFIX}_PORT=8095
{PREFIX}_API_URL=https://api.ustartkpay.com
{PREFIX}_DB=/opt/ustartkpay/data/ustartkpay.db

# The account key is NOT here. Only its path is. The engine reads the
# 0600 file directly so the key never lands in /proc/<pid>/environ.
{PREFIX}PAY_ACCOUNT_KEY=/opt/ustartkpay/secrets/account.json

# The treasury address. Empty on purpose: the MAINNET account does
# not exist yet, and an address that does not exist is not a lie the
# dashboard can publish. The engine runs read-only until it does.
{PREFIX}_WALLET_ADDRESS=

# The treasury's own address, where the fee goes.
{PREFIX}_REVENUE_ADDRESS=

# Only this whitelabel's own origins. The main site is not allowed.
{PREFIX}_CORS_ORIGINS=https://ustartkpay.com,https://www.ustartkpay.com

# Fees and limits. Starknet gas is paid in STRK.
{PREFIX}_FEE_PCT=0.5
{PREFIX}_FEE_MIN_USDC=0.1
{PREFIX}_DEFAULT_DAILY_LIMIT=500
{PREFIX}_DEFAULT_DUAL_THRESHOLD=1000

# Generated for this deployment, not copied from another one.
{PREFIX}_JWT_SECRET={secrets.token_hex(32)}
{PREFIX}_VOICE_SECRET={secrets.token_hex(32)}

{PREFIX}_DAY_TZ_OFFSET=0
{PREFIX}_SETTLING_MAX_AGE_S=300
"""

with open(PATH, "w", encoding="utf-8") as fh:
    fh.write(env)
os.chmod(PATH, 0o600)

print(f"{PATH}: written, mode 0600")
print("  the JWT and voice secrets are generated, not copied")
print(f"  the treasury address is empty - the mainnet account does not exist yet")
print(f"  the Sepolia account is {SEP_ADDR}")
