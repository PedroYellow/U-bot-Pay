#!/usr/bin/env python3
"""Make the profile loader's revenue address chain-appropriate.

The test hardcoded a Solana base58 address as the revenue address:

    REVENUE = "AgbmMLTnmEKN4FoTfim4y9bdjxqWGLs9mwf3MTSe3CbJ"

The EVM engine correctly rejects it - a base58 address is not an EVM
address - so the assertion "engine validates it as a Solana address"
failed on Sonic for the right reason and the wrong message.

The address now depends on the deployment's chain, and the assertion
says what it actually checks: that the engine validates an address on
THIS chain.

Usage: fix_revenue_address_test.py <test.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/test_profile_loader.py"

OLD_ADDR = 'REVENUE = "AgbmMLTnmEKN4FoTfim4y9bdjxqWGLs9mwf3MTSe3CbJ"'

NEW_ADDR = '''# A revenue address that is valid on THIS deployment's chain. The value
# was a Solana base58 address, which the EVM engine correctly rejects -
# the assertion was checking Solana on a Sonic deployment.
REVENUE = os.environ.get(
    "TEST_REVENUE_ADDRESS",
    "AgbmMLTnmEKN4FoTfim4y9bdjxqWGLs9mwf3MTSe3CbJ"
    if _DEPLOYMENT_KIND == "solana" else
    "0xCC57d2f8A941B3408f939E6fe949fb7C2b1F01Af")'''

OLD_CHK = 'chk("engine validates it as a Solana address", eng.is_address(eng._revenue))'

NEW_CHK = '''chk("engine validates it as an address on this chain",
    eng.is_address(eng._revenue), repr(eng._revenue))'''

src = open(path, encoding="utf-8").read()
n = 0
if OLD_ADDR in src:
    src = src.replace(OLD_ADDR, NEW_ADDR, 1)
    n += 1
else:
    print("  the hardcoded revenue address was not found")
if OLD_CHK in src:
    src = src.replace(OLD_CHK, NEW_CHK, 1)
    n += 1
else:
    print("  the address assertion was not found")

open(path, "w", encoding="utf-8").write(src)
print(f"{path}: {n} change(s)")
