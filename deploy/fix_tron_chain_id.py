#!/usr/bin/env python3
"""Give the Tron profile and engine a real chain id.

Three of the four deployments report their chain id correctly:

    8090  avalanche-mainnet   43114
    8093  USolPay             1151111081099710
    8094  Sonic               146

The Tron deployment reported:

    8092  Tron Mainnet        0

Two places said zero:

    /opt/utronpay/profiles/utronpay.yaml   no chain_id key
    /opt/utronpay/chains/tron.py            self._chain_id = 0

Tron mainnet is 728126428. The chain id is not cosmetic: it is what
the x402 payment paths sign over, what a bridge route is keyed by, and
what the dashboard publishes as this deployment's identity. A
deployment reporting 0 is a deployment that cannot be told apart from
"no chain".

The other three deployments get it from their own chain definition.
The EVM engine takes `chain_id` as a constructor argument and the
registry passes it; the Tron engine never did, so the registry has
nothing to pass and the value defaulted to zero.

The fix follows the same shape the EVM engine already uses: the
profile carries the key, the registry passes it, the engine keeps it.

Usage: fix_tron_chain_id.py <profile.yaml> <tron.py> <registry.py>
"""
import sys

profile_path = sys.argv[1] if len(sys.argv) > 1 else "/opt/utronpay/profiles/utronpay.yaml"
tron_path = sys.argv[2] if len(sys.argv) > 2 else "/opt/utronpay/chains/tron.py"
registry_path = sys.argv[3] if len(sys.argv) > 3 else "/opt/utronpay/chains/registry.py"

TRON_MAINNET = 728126428
n = 0

# ── 1. the profile ────────────────────────────────────────────────
src = open(profile_path, encoding="utf-8").read()

if "chain_id:" not in src:
    OLD = '''chains:
  kind: tron
  network: mainnet
  stable_symbol: USDT
  native_symbol: TRX'''

    NEW = '''chains:
  kind: tron
  network: mainnet
  # Tron mainnet's chain id. The other three deployments report theirs
  # correctly; this one reported 0, because the profile had no key and
  # the engine defaulted to zero. The chain id is not cosmetic - it is
  # what the x402 paths sign over and what identifies this deployment.
  chain_id: 728126428
  stable_symbol: USDT
  native_symbol: TRX'''

    if OLD in src:
        src = src.replace(OLD, NEW, 1)
        open(profile_path, "w", encoding="utf-8").write(src)
        print(f"{profile_path}: chain_id {TRON_MAINNET} added")
        n += 1
    else:
        print(f"{profile_path}: the chains block was not found in the expected form")
else:
    print(f"{profile_path}: it already has a chain_id")

# ── 2. the engine takes the chain id ──────────────────────────────
src = open(tron_path, encoding="utf-8").read()

OLD_SIG = '''    def __init__(self, *, network: str = "mainnet", rpc: str | None = None,
                 explorer: str | None = None, stable_address: str | None = None,
                 private_key_hex: str | None = None,
                 revenue_address: str | None = None,
                 api_key: str | None = None,
                 fallback_rpcs: list | None = None):'''

NEW_SIG = '''    def __init__(self, *, network: str = "mainnet", rpc: str | None = None,
                 explorer: str | None = None, stable_address: str | None = None,
                 private_key_hex: str | None = None,
                 revenue_address: str | None = None,
                 api_key: str | None = None,
                 chain_id: int | None = None,
                 fallback_rpcs: list | None = None):'''

if OLD_SIG in src:
    src = src.replace(OLD_SIG, NEW_SIG, 1)
    n += 1
else:
    print(f"{tron_path}: the constructor signature was not found")

OLD_ID = '''        self._chain_id = 0'''

NEW_ID = '''        # The chain id comes from the profile, like every other
        # chain-specific value. It used to be hardcoded to 0, so the
        # deployment reported chain_id 0 - indistinguishable from "no
        # chain", and wrong for anything that keys on it.
        self._chain_id = int(chain_id or 0)'''

if OLD_ID in src:
    src = src.replace(OLD_ID, NEW_ID, 1)
    n += 1
else:
    print(f"{tron_path}: the hardcoded chain id was not found")

open(tron_path, "w", encoding="utf-8").write(src)
print(f"{tron_path}: the engine takes the chain id")

# ── 3. the registry passes it ─────────────────────────────────────
src = open(registry_path, encoding="utf-8").read()

OLD_REG = '''            api_key=spec.get("api_key"),
            fallback_rpcs=spec.get("fallback_rpcs") or [],
        )'''

NEW_REG = '''            api_key=spec.get("api_key"),
            chain_id=spec.get("chain_id"),
            fallback_rpcs=spec.get("fallback_rpcs") or [],
        )'''

if OLD_REG in src:
    src = src.replace(OLD_REG, NEW_REG, 1)
    open(registry_path, "w", encoding="utf-8").write(src)
    print(f"{registry_path}: the chain id is passed to the Tron engine")
    n += 1
else:
    print(f"{registry_path}: the TronEngine call was not found")

print(f"{n} change(s)")
