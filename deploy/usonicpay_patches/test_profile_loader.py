"""
Regression tests for the profile loader and the revenue address.

Three bugs lived here, all of them silent -- the service started, the
health check passed, and the thing that was supposed to work simply
did not:

1. The env override never reached the chain spec. The loader iterated
   `for spec in prof["chains"]`, and `chains` is a dict, so the loop
   produced the dict's KEYS (strings) and `isinstance(spec, dict)` was
   always False. The revenue address, the private key and the API key
   were therefore never written into the profile. A deployment with a
   valid USOL_REVENUE_ADDRESS in its environment charged fees and paid
   them nowhere.

2. The Solana engine read `profile["revenue_address"]` while the
   loader wrote `profile["chains"]["revenue_address"]`. Two different
   levels of the same document, so the address never reached the
   engine even once the loader worked.

3. A non-empty `revenue_address: ""` in the profile file (a string with
   a trailing comment) shadowed the environment value, because
   `if prof.get("revenue_address")` is True for any non-empty string
   -- including one that is empty of content.

These tests run against the real loader and the real engine. They need
no network and no funded wallet.
"""
import os
import sys

# The deployment this test belongs to, by default. The default used
# to be /opt/usolpay - the deployment these tests were forked from -
# so in a whitelabel they loaded the WRONG deployment's registry and
# profile and then failed on its missing dependencies. The default is
# now the directory this file lives in, so a test in a deployment tests
# that deployment.
sys.path.insert(0, os.environ.get("ENGINE_ROOT", os.path.dirname(
    os.path.abspath(__file__))))

from chains.registry import _load_yaml, load_profile, build_engine
from config import cfg, pop_cfg, set_cfg, del_cfg

# ---------------------------------------------------------------------
# Deployment-aware facts
#
# These tests were forked from the Solana deployment. The assertions
# below describe Solana, so they were failing on an EVM chain for
# reasons that were not defects. They now ask the deployment what it
# is instead of assuming.
# ---------------------------------------------------------------------
def _deployment_facts():
    """The chain this deployment runs, read from its own profile."""
    try:
        from chains.registry import load_profile
        prof = load_profile(PROFILE if "PROFILE" in dir() else None)
        spec = (prof.get("chain") or {}) if isinstance(prof, dict) else {}
        cid = spec.get("chain_id")
        kind = (spec.get("kind") or spec.get("type") or "evm").lower()
        return cid, kind
    except Exception:
        return None, None


try:
    _DEPLOYMENT_CHAIN_ID, _DEPLOYMENT_KIND = _deployment_facts()
except Exception:
    _DEPLOYMENT_CHAIN_ID, _DEPLOYMENT_KIND = None, None

# The balance field name differs by engine: the Solana engine calls it
# "settled", the EVM engine calls it "usdc".
_DEPLOYMENT_BALANCE_FIELD = "settled" if _DEPLOYMENT_KIND == "solana" else "usdc"

# The address shape: Solana is base58, EVM is 0x + 40 hex.
_DEPLOYMENT_IS_EVM = _DEPLOYMENT_KIND != "solana"


RESULTS = []


def chk(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print("%s  %-56s %s" % ("PASS" if cond else "FAIL", name, detail))


# This deployment's own profile. The default used to be the Solana
# deployment's profile, so in a whitelabel the test loaded a Solana
# profile and asked for a Solana engine.
_PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")
_PROFILE_NAME = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.environ.get(
    "PROFILE_PATH",
    os.path.join(_PROFILE_DIR, _PROFILE_NAME + ".yaml"))
# A revenue address that is valid on THIS deployment's chain. The value
# was a Solana base58 address, which the EVM engine correctly rejects -
# the assertion was checking Solana on a Sonic deployment.
REVENUE = os.environ.get(
    "TEST_REVENUE_ADDRESS",
    "AgbmMLTnmEKN4FoTfim4y9bdjxqWGLs9mwf3MTSe3CbJ"
    if _DEPLOYMENT_KIND == "solana" else
    "0xCC57d2f8A941B3408f939E6fe949fb7C2b1F01Af")

# ── 1. the loader writes the env override into the chain spec ──
set_cfg("REVENUE_ADDRESS", REVENUE)
prof = load_profile(PROFILE)

chk("profile loads", isinstance(prof, dict), type(prof).__name__)
chains = prof.get("chains")
chk("chains is a dict (single spec)", isinstance(chains, dict), type(chains).__name__)

chk("env override reached the chain spec",
    chains.get("revenue_address") == REVENUE,
    repr(chains.get("revenue_address")))

# The bug this guards: iterating a dict yields keys, so the override
# was silently dropped.
chk("the spec is still a dict after the override (not a key string)",
    isinstance(chains, dict) and "revenue_address" in chains)

# ── 2. the engine reads it ──
eng = build_engine(chains)
chk("engine has the revenue address", eng._revenue == REVENUE, repr(eng._revenue))
chk("engine validates it as an address on this chain",
    eng.is_address(eng._revenue), repr(eng._revenue))

# ── 3. the profile file must not carry a shadowing revenue_address ──
raw = _load_yaml(PROFILE)
chk("profile file has no top-level revenue_address",
    not raw.get("revenue_address"),
    repr(raw.get("revenue_address")))

# ── 4. a private key in the env must not be looked for in the file ──
set_cfg("PRIVATE_KEY", "deadbeef" * 8)
prof2 = load_profile(PROFILE)
chk("private_key env override reaches the spec",
    prof2["chains"].get("private_key") == "deadbeef" * 8)
del_cfg("PRIVATE_KEY")

# ── 5. without the env var, the spec carries no revenue address ──
saved = pop_cfg("REVENUE_ADDRESS", None)
prof3 = load_profile(PROFILE)
chk("without the env var there is no revenue address",
    not prof3["chains"].get("revenue_address"),
    repr(prof3["chains"].get("revenue_address")))
if saved:
    set_cfg("REVENUE_ADDRESS", saved)

# ── 6. the engine reports whether it can sign ──
eng2 = build_engine(prof2["chains"])
chk("engine exposes has_signer()", hasattr(eng2, "has_signer"))
chk("has_signer is a bool", isinstance(eng2.has_signer(), bool),
    eng2.has_signer())

print("=" * 86)
print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
print("ALL LOADER TESTS PASSED" if all(RESULTS) else "SOME LOADER TESTS FAILED")
sys.exit(0 if all(RESULTS) else 1)