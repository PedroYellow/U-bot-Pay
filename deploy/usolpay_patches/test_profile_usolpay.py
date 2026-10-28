"""
Verify profiles/usolpay.yaml parses through the registry's mini-YAML
fallback (PyYAML is not installed in the runtime venv).

Checks the shape the Solana engine will actually depend on, so a typo in
the profile surfaces here rather than at first payment.
"""
import os
import sys

sys.path.insert(0, "/opt/usolpay" if os.path.isdir("/opt/usolpay")
                else os.path.dirname(os.path.abspath(__file__)))

from chains.registry import _load_yaml

PATH = (sys.argv[1] if len(sys.argv) > 1
        else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "profiles", "usolpay.yaml"))

RESULTS = []


def chk(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print("%s  %-52s %s" % ("PASS" if cond else "FAIL", name, detail))


prof = _load_yaml(os.environ.get(
        "PROFILE_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "profiles", "usolpay.yaml")))

chk("profile loads as a dict", isinstance(prof, dict), "type=%s" % type(prof).__name__)
# The profile follows the registry's shape: `name`, `brand`, `engine`,
# and a `chains:` block. The Solana-specific settings the engine reads
# live at the top level (token, rpc, ata_policy...) and in the chains
# block (kind, stable_address).
chk("brand is usolpay", prof.get("brand") == "usolpay", repr(prof.get("brand")))
chk("engine is solana", prof.get("engine") == "solana", repr(prof.get("engine")))
chk("name is set", bool(prof.get("name")), repr(prof.get("name")))
chains = prof.get("chains") or {}
chk("chains.kind is solana", chains.get("kind") == "solana", repr(chains.get("kind")))
chk("chains has a stable_address",
    chains.get("stable_address") == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    repr(chains.get("stable_address")))

tok = prof.get("token") or {}
chk("token.symbol == USDC", tok.get("symbol") == "USDC", repr(tok.get("symbol")))
chk("token.decimals == 6", tok.get("decimals") == 6, repr(tok.get("decimals")))
chk("token.mint is the mainnet USDC SPL mint",
    tok.get("mint") == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    repr(tok.get("mint")))

rpc = prof.get("rpc") or {}
chk("rpc.url present", bool(rpc.get("url")), repr(rpc.get("url")))
chk("rpc.confirm_timeout is an int", isinstance(rpc.get("confirm_timeout"), int),
    repr(rpc.get("confirm_timeout")))

chk("supports_x402 is False (SPL has no transferWithAuthorization)",
    prof.get("supports_x402") is False, repr(prof.get("supports_x402")))
chk("ata_policy == create", prof.get("ata_policy") == "create", repr(prof.get("ata_policy")))
chk("fee_token == USDC", prof.get("fee_token") == "USDC", repr(prof.get("fee_token")))
chk("address_format == base58", prof.get("address_format") == "base58",
    repr(prof.get("address_format")))
chk("tx_hash_format == base58_88", prof.get("tx_hash_format") == "base58_88",
    repr(prof.get("tx_hash_format")))

print("=" * 78)
print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
print("PROFILE OK" if all(RESULTS) else "PROFILE BROKEN")
sys.exit(0 if all(RESULTS) else 1)