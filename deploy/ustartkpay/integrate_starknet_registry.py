#!/usr/bin/env python3
"""Teach the registry about Starknet.

The registry builds one engine from a profile's `chains:` block, and
it dispatches on `kind`:

    if kind == "tron":  ...
    if kind == "evm":   ...
    if kind == "solana": ...

Starknet is neither. It is a Cairo chain with account abstraction, so
it needs its own engine - and the profile carries a STRING chain id
("SN_MAIN"), because Starknet has no numeric CAIP-2 chain id.

So this adds:

  1. the `starknet` branch in build_engine
  2. the `starknet` case in _sdk_for, so the conflict check knows
     starknet_py is its own SDK

The conflict table itself needs no entry. _INCOMPATIBLE_SDKS is a set
of PAIRS - {web3, tronpy} - and starknet_py conflicts with neither:
it pins no eth-abi, so it can share an interpreter with web3 or with
tronpy. Adding a pair for it would be wrong: it would forbid a
combination that is fine.

The engine file is copied into the deployment's chains/ package as
`starknet.py`, so `from .starknet import StarknetEngine` works.

Usage: integrate_starknet_registry.py <registry.py> <starknet-engine.py>
"""
import os
import shutil
import sys

registry_path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/chains/registry.py"
engine_src = sys.argv[2] if len(sys.argv) > 2 else \
    "/data/workspace/ubotpay/deploy/ustartkpay/chains_starknet_engine.py"

src = open(registry_path, encoding="utf-8").read()
n = 0

# ── 1. the build_engine branch ─────────────────────────────────────
OLD_BUILD = '''    if kind == "evm":
        from .evm import EvmEngine'''

NEW_BUILD = '''    if kind == "starknet":
        # Starknet is not EVM. It is a Cairo chain with account
        # abstraction: an account is a CONTRACT, the address is derived
        # from (public key, salt, class hash), and the contract has to
        # be deployed at it before it can sign anything.
        #
        # The chain id is a STRING - "SN_MAIN" - because Starknet has
        # no numeric CAIP-2 chain id. So it is not int()'d here.
        from .starknet import StarknetEngine
        return StarknetEngine(
            rpc=spec["rpc"],
            stable_address=spec["stable_address"],
            explorer=spec.get("explorer", ""),
            native_symbol=spec.get("native_symbol", "STRK"),
            account_address=spec.get("account_address"),
            # The key file, by path. The key is never in the
            # environment: an env var is visible in /proc/<pid>/environ,
            # in `ps eww` and in crash dumps.
            key_file=spec.get("key_file"),
            fee_token_address=spec.get(
                "fee_token_address",
                "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d"),
            rpc_urls=spec.get("rpc_urls") or [],
        )
    if kind == "evm":
        from .evm import EvmEngine'''

if OLD_BUILD in src:
    src = src.replace(OLD_BUILD, NEW_BUILD, 1)
    n += 1
else:
    print("  the build_engine branch was not found")

# ── 2. the sdk check ───────────────────────────────────────────────
OLD_SDK = '''    kind = (spec.get("kind") or spec.get("type") or "evm").lower()
    if kind == "tron":
        return "tronpy"'''

NEW_SDK = '''    kind = (spec.get("kind") or spec.get("type") or "evm").lower()
    if kind == "starknet":
        # starknet_py. It pins no eth-abi, so it does not conflict
        # with web3 or tronpy - the conflict table needs no entry for
        # it. It is still NAMED here, because the check reasons about
        # pairs and an unnamed SDK would be treated as compatible with
        # everything by accident rather than by decision.
        return "starknet_py"
    if kind == "tron":
        return "tronpy"'''

if OLD_SDK in src:
    src = src.replace(OLD_SDK, NEW_SDK, 1)
    n += 1
else:
    print("  the _sdk_for branch was not found")

open(registry_path, "w", encoding="utf-8").write(src)

# ── 3. the engine into the package ─────────────────────────────────
dst = os.path.join(os.path.dirname(registry_path), "starknet.py")
shutil.copyfile(engine_src, dst)

try:
    compile(src, registry_path, "exec")
    print(f"{registry_path}: {n} change(s), it parses")
except SyntaxError as e:
    print(f"{registry_path}: {n} change(s) but it does NOT parse: {e}")
    sys.exit(1)

print(f"{dst}: the Starknet engine is in the package")
