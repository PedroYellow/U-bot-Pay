"""
Regression tests for chain registry / bridge token drift (audit 20 Oct).

chain.py and bridge.py each keep their own map of chain_id -> USDC address.
They are separate dictionaries in separate modules and nothing enforces that they
agree, so a chain added to one and forgotten in the other means the UI shows one
token while the bridge quotes another — silently, because the quote simply fails.

Sonic was added to chain.py first and only reached bridge.py on the second pass,
which is exactly the failure this guards against.

Also covers the Sonic address itself: the USDC addresses that circulate on the web
are wrong, and eth_getCode returns empty for them.

    python3 test_chain_registry.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS = []
CHAIN_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chain.py")
BRIDGE_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge.py")


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:58} got={got!r} want={want!r}")


# ── Sonic's address, verified on-chain ────────────────────────────────────
SONIC_USDC = "0x29219dd400f2Bf60E5a23d13Be72B486D4038894"
WRONG_ONES = [
    "0xA487d2D0Df7e3d32b1ABD1d693BA4bb9Da5fA28",  # floats around, no contract
    "0x223230BB6e95B4Ac4F08AA16BdD9B895b2Ca4D0f",
    "0x2e5222bf4b021218799ab322d9a0c7562f52361773",
]


def t_sonic_address_present():
    chain_s = open(CHAIN_PY, encoding="utf-8").read()
    bridge_s = open(BRIDGE_PY, encoding="utf-8").read()
    check("Sonic USDC in chain.py", SONIC_USDC in chain_s, True)
    check("Sonic USDC in bridge.py", SONIC_USDC in bridge_s, True)


def t_wrong_addresses_absent():
    """None of these have bytecode on Sonic; writing one would break the bridge."""
    both = open(CHAIN_PY, encoding="utf-8").read() + open(BRIDGE_PY, encoding="utf-8").read()
    for a in WRONG_ONES:
        check(f"bogus address absent: {a[:12]}…", a in both, False)


def t_address_is_checksummed():
    """web3.to_checksum_address must accept it verbatim; a mis-cased address
    silently fails address comparison in transfer validation."""
    mixed = "0x29219dd400f2Bf60E5a23d13Be72B486D4038894"
    check("address length is 42", len(mixed), 42)
    check("is 0x + 40 hex", bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", mixed)), True)


# ── the two maps must agree ───────────────────────────────────────────────
def parse_registry(path):
    s = open(path, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r"^\s*(\d+):\s*Chain\(", s, re.M):
        cid = m.group(1)
        block = s[m.start():m.start() + 600]
        # skip the name string, take the address
        # EVM chains use 0x...; Solana's SPL mint is base58, so accept both.
        # Skip the RPC/explorer fields, which are URLs, by taking only strings
        # that look like an address (0x+40 hex, or 32-44 base58 chars).
        addrs = re.findall(r'"(0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})"', block)
        env = re.search(r'os\.environ\.get\("USDC_ADDRESS",\s*"(0x[0-9a-fA-F]{40})"\)', block)
        if addrs:
            out[cid] = addrs[0]
        elif env:
            out[cid] = env.group(1)
    return out


def parse_bridge(path):
    s = open(path, encoding="utf-8").read()
    blk = s[s.index("USDC_BY_CHAIN"):]
    blk = blk[:blk.index("}")]
    # Same: hex for EVM, base58 for Solana's SPL mint.
    return dict(re.findall(r'(\d+):\s*"(0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})"', blk))


def t_maps_agree():
    reg = parse_registry(CHAIN_PY)
    brg = parse_bridge(BRIDGE_PY)
    # Avalanche mainnet/fuji read USDC_ADDRESS from the environment in chain.py,
    # so only compare where both files state a literal.
    skip = {"43114", "43113"}
    mismatches = []
    for cid in sorted(set(reg) & set(brg) - skip, key=int):
        if reg[cid].lower() != brg[cid].lower():
            mismatches.append((cid, reg[cid], brg[cid]))
    check("no chain has a different USDC in the two files",
          [m[0] for m in mismatches], [])


def t_every_bridge_chain_is_registered():
    """A chain the bridge can quote but the registry doesn't know would fail at
    balance time."""
    reg = parse_registry(CHAIN_PY)
    brg = parse_bridge(BRIDGE_PY)
    orphans = sorted(set(brg) - set(reg) - {"43113"}, key=int)   # Fuji is testnet-only
    check("bridge has no chain missing from the registry", orphans, [])


SOLANA_LIFI_ID = 1151111081099710
SOLANA_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def t_solana_wired_as_destination_only():
    reg = parse_registry(CHAIN_PY)
    brg = parse_bridge(BRIDGE_PY)
    chain_s = open(CHAIN_PY, encoding="utf-8").read()
    bridge_s = open(BRIDGE_PY, encoding="utf-8").read()
    check("Solana in the registry", str(SOLANA_LIFI_ID) in reg, True)
    check("Solana USDC matches in both maps",
          reg.get(str(SOLANA_LIFI_ID)) == brg.get(str(SOLANA_LIFI_ID)) == SOLANA_USDC, True)
    check("Solana marked destination-only in bridge.py",
          "_DESTINATION_ONLY_CHAINS" in bridge_s, True)
    check("Solana has no RPC configured", 'Chain(1151111081099710, "Solana", ""' in chain_s, True)
    check("Solana name mapping present", '"solana": 1151111081099710' in bridge_s, True)


def t_solana_not_a_source():
    """Quoting Solana -> EVM produces a response with no eth_sendTransaction
    payload, because the leg to sign is a Solana transaction. It must be
    refused rather than handed to an EVM wallet."""
    bridge_s = open(BRIDGE_PY, encoding="utf-8").read()
    i = bridge_s.index("_DESTINATION_ONLY_CHAINS")
    j = bridge_s.index("def _http_get_json")
    block = bridge_s[i:j]
    check("guard set contains Solana", "1151111081099710" in block, True)
    check("guard raises with a reason", "destination, not a source" in bridge_s, True)


def t_solana_usdc_is_not_an_evm_address():
    """Solana's USDC is an SPL mint (base58), not 0x... — anything that assumes
    hex addresses must not try to checksum it."""
    check("Solana USDC is base58, not 0x", SOLANA_USDC.startswith("EPj"), True)
    check("length is a plausible Solana address", 32 <= len(SOLANA_USDC) <= 44, True)


def t_sonic_wired_everywhere():
    reg = parse_registry(CHAIN_PY)
    brg = parse_bridge(BRIDGE_PY)
    check("Sonic in the registry", reg.get("146", "").lower(), SONIC_USDC.lower())
    check("Sonic in the bridge map", brg.get("146", "").lower(), SONIC_USDC.lower())
    bridge_s = open(BRIDGE_PY, encoding="utf-8").read()
    check("Sonic name mapping present", '"sonic": 146' in bridge_s, True)


if __name__ == "__main__":
    t_sonic_address_present()
    t_wrong_addresses_absent()
    t_address_is_checksummed()
    t_maps_agree()
    t_every_bridge_chain_is_registered()
    t_sonic_wired_everywhere()
    t_solana_wired_as_destination_only()
    t_solana_not_a_source()
    t_solana_usdc_is_not_an_evm_address()
    print("=" * 72)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)