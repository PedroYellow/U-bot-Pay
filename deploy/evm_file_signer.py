#!/usr/bin/env python3
"""Give the EVM engine a file-based signer, like the Solana engine has.

The EVM engine takes `private_key_hex` as a string, so the caller has to
hold the key in memory and normally passes it through the environment.
That is what the Solana engine was built to avoid: an exported secret
lands in /proc/<pid>/environ, which is world-readable on the host and
shows up in `ps eww`, `systemctl show`, and crash dumps.

This gives the EVM engine the same property - it reads the 0600 key
file itself - by:

  1. adding a `key_path` parameter to EvmEngine, read at construction
  2. adding `has_signer()` so the startup check can ask whether the
     deployment can sign at all
  3. teaching build_engine to prefer the file over the env value

The key never enters the environment, and never enters a process
argument list.

Usage: evm_file_signer.py <tree-dir>
"""
import os
import re
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay"
evm = os.path.join(root, "chains", "evm.py")
reg = os.path.join(root, "chains", "registry.py")

# ── 1. the engine ────────────────────────────────────────────────────────
s = open(evm, encoding="utf-8").read()

OLD_INIT = '''    def __init__(self, *, chain_id: int, name: str, rpc: str,
                 stable_address: str, explorer: str, native_symbol: str = "ETH",
                 private_key_hex: str | None = None,
                 revenue_address: str | None = None,
                 stable_symbol: str | None = None,
                 gas: int = 90_000, max_fee_gwei: int = 30,
                 priority_gwei: int = 1, timeout: int = 20):'''

NEW_INIT = '''    def __init__(self, *, chain_id: int, name: str, rpc: str,
                 stable_address: str, explorer: str, native_symbol: str = "ETH",
                 private_key_hex: str | None = None,
                 revenue_address: str | None = None,
                 stable_symbol: str | None = None,
                 key_path: str | None = None,
                 gas: int = 90_000, max_fee_gwei: int = 30,
                 priority_gwei: int = 1, timeout: int = 20):'''

if OLD_INIT in s:
    s = s.replace(OLD_INIT, NEW_INIT, 1)
else:
    print("evm.py: constructor signature not found")

OLD_KEY = '''        self._key_hex = private_key_hex or ""'''
NEW_KEY = '''        # The key comes from a 0600 file when a path is given, not from
        # the environment. An exported secret lands in /proc/<pid>/environ,
        # which is world-readable on the host and shows up in `ps eww`,
        # `systemctl show`, and crash dumps.
        self._key_path = key_path or ""
        self._key_hex = ""
        if key_path:
            try:
                with open(key_path, "r", encoding="utf-8") as fh:
                    self._key_hex = fh.read().strip()
            except OSError:
                # A missing key must not stop the service from booting:
                # before the treasury is funded there is nothing to sign,
                # and the read paths are useful. Sends are refused with a
                # message that names the file.
                log.warning(
                    "no treasury key at %s; payments cannot be signed", key_path)
        elif private_key_hex:
            self._key_hex = private_key_hex
        if self._key_hex and not self._key_hex.startswith("0x"):
            self._key_hex = "0x" + self._key_hex'''

if OLD_KEY in s:
    s = s.replace(OLD_KEY, NEW_KEY, 1)
else:
    print("evm.py: key assignment not found")

# has_signer()
if "def has_signer" not in s:
    s += '''
    # ── can this deployment sign? ──
    def has_signer(self) -> bool:
        """Whether a treasury key is available.

        A read-only deployment does not have one, and saying so plainly
        is more useful than a warning about an env var that by design
        never holds the key.
        """
        return bool(self._key_hex)

    def treasury_address(self) -> str:
        """The treasury's own address, as this engine sees it."""
        if not self._key_hex:
            return ""
        try:
            from eth_account import Account
            return Account.from_key(self._key_hex).address
        except Exception:
            return ""
'''
    print("evm.py: has_signer() added")

open(evm, "w", encoding="utf-8").write(s)

# ── 2. the registry ──────────────────────────────────────────────────────
s = open(reg, encoding="utf-8").read()

OLD_BUILD = '''        return EvmEngine(
            chain_id=int(spec["chain_id"]),
            name=spec.get("name") or f"Chain {spec['chain_id']}",
            rpc=spec["rpc"],
            stable_address=spec["stable_address"],
            explorer=spec.get("explorer", ""),
            native_symbol=spec.get("native", "ETH"),
            private_key_hex=spec.get("private_key"),
            revenue_address=spec.get("revenue_address"),
            stable_symbol=spec.get("stable_symbol", "USDC"),
        )'''

NEW_BUILD = '''        return EvmEngine(
            chain_id=int(spec["chain_id"]),
            name=spec.get("name") or f"Chain {spec['chain_id']}",
            rpc=spec["rpc"],
            stable_address=spec["stable_address"],
            explorer=spec.get("explorer", ""),
            native_symbol=spec.get("native", "ETH"),
            # The key file wins over the environment value: the file is
            # the one that can be kept at 0600.
            key_path=spec.get("key_path"),
            private_key_hex=spec.get("private_key"),
            revenue_address=spec.get("revenue_address"),
            stable_symbol=spec.get("stable_symbol", "USDC"),
        )'''

if OLD_BUILD in s:
    s = s.replace(OLD_BUILD, NEW_BUILD, 1)
    open(reg, "w", encoding="utf-8").write(s)
    print("registry.py: key_path wired")
else:
    print("registry.py: EvmEngine construction not found")

print("done")
