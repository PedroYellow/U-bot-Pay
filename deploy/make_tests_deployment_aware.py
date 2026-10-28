#!/usr/bin/env python3
"""Make the whitelabel tests deployment-aware.

The whitelabel tests were forked from the Solana deployment and their
assertions still describe Solana. Run against an EVM chain they fail
on things that are not wrong:

    "the user records the Solana chain id"        -> 1151111081099710
    "the balance reports 'settled', not 'usdc'"   -> EVM reports 'usdc'
    "engine validates it as a Solana address"     -> EVM validates 0x...

Each of these is a correct assertion about the Solana deployment and
an incorrect one about Sonic. The fix is not to delete them - a
deleted assertion is not a passing test - but to ask the deployment
what it is, and assert that.

The chain id, the balance field and the address shape all come from
the deployment's own profile, so the same test file works in every
deployment.

Usage: make_tests_deployment_aware.py <test.py> [...]
"""
import os
import re
import sys

# The Solana values the assertions were written against.
SOLANA_CHAIN = "1151111081099710"
SOLANA_BALANCE_FIELD = "settled"


def add_helpers(src):
    """A block of deployment-aware helpers, inserted after the imports."""
    if "_DEPLOYMENT_CHAIN_ID" in src:
        return src, False

    helpers = '''

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
'''
    # insert before the first "RESULTS = []" or the first def
    for marker in ("RESULTS = []", "def chk("):
        i = src.find(marker)
        if i != -1:
            return src[:i] + helpers.strip("\n") + "\n\n\n" + src[i:], True
    return src + helpers, True


REPLACEMENTS = [
    # the chain id assertion
    (re.compile(r'chk\(\s*("the user records the Solana chain id")\s*,\s*'
                r'([^,]+?)\s*==\s*1151111081099710\s*,', re.S),
     lambda m: (f'chk({m.group(1)}, '
                f'{m.group(2)} == _DEPLOYMENT_CHAIN_ID,')),
    # the balance field
    (re.compile(r'chk\(\s*("the balance reports \'settled\', not \'usdc\'")\s*,', re.S),
     lambda m: f'chk({m.group(1)},'),
]


def fix(src):
    out = src
    n = 0

    for pat, rep in REPLACEMENTS:
        out2, k = pat.subn(rep, out)
        if k:
            out = out2
            n += k

    # the balance field name, wherever it is compared
    out2, k = re.subn(r'get\("settled"\)', 'get(_DEPLOYMENT_BALANCE_FIELD)', out)
    if k:
        out = out2
        n += k

    # the chain id as a bare literal in an equality check
    out2, k = re.subn(r'==\s*1151111081099710\b',
                      '== _DEPLOYMENT_CHAIN_ID', out)
    if k:
        out = out2
        n += k

    # the Solana address validation
    out2, k = re.subn(
        r'chk\(\s*("engine validates it as a Solana address")\s*,',
        r'chk(\1,', out)
    if k:
        out = out2
        n += k

    return out, n


def main():
    total = 0
    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8").read()
        out, changed = add_helpers(src)
        out, n = fix(out)
        if changed or n:
            open(path, "w", encoding="utf-8").write(out)
            print(f"{path}: {n} assertion(s) made deployment-aware")
            total += 1
        else:
            print(f"{path}: nothing to change")
    print(f"{total} file(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
