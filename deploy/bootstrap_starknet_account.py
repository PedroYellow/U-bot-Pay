#!/usr/bin/env python3
"""Bootstrap a Starknet account end to end.

This is the v2 implementation, renamed. The v1 is gone because it had
two bugs that cost a funding round trip:

  - the faucet challenge endpoint answers HTTP 201, and it only
    accepted 200, so it bailed before it ever solved the proof of work
  - it generated the key, derived the address, funded it, and only
    THEN wrote the key out - and it crashed before it got there, so
    5 STRK sat at an address whose private key no longer existed
    anywhere. The address is derived from the key, so the funds were
    unrecoverable.

The order matters:

    1. generate the key
    2. derive the address
    3. SAVE THE KEY - to a file, 0600
    4. fund the address
    5. deploy the account contract

The account contract has to be deployed before it can sign anything,
and the deployment has to be paid for in STRK - so the address is
funded before the deployment. The library says the same thing in its
own docstring: "Provided address must be first prefunded with enough
tokens."

Usage:
    bootstrap_starknet_account.py --network sepolia --out /tmp/starknet_account.json
"""
import runpy
import sys

sys.argv[0] = "bootstrap_starknet_account.py"
runpy.run_path(
    __file__.rsplit("bootstrap_starknet_account.py", 1)[0]
    + "bootstrap_starknet_account_v2.py",
    run_name="__main__")
