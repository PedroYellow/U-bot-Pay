#!/usr/bin/env python3
"""Pass the network explicitly when the registry builds the Starknet engine.

The engine validates the key file's network against the deployment's
network, but the registry never told the engine which network it was
serving - so the engine used its own default of "mainnet".

That default is right for this deployment and wrong for a sepolia one,
and a silent default is exactly how a sepolia key ended up on a
mainnet deployment in the first place. So the network goes through
the profile and into the constructor.

Usage: patch_registry_network.py <registry.py>
"""
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/opt/ustartkpay/chains/registry.py"

OLD = '''            key_file=spec.get("key_file"),
            fee_token_address=spec.get('''

NEW = '''            key_file=spec.get("key_file"),
            # The network this deployment serves. The key file names its
            # own network and the two have to agree, so it is passed
            # explicitly rather than left to the engine's default.
            # Without it the engine defaults to mainnet, which is right
            # for this deployment and wrong for a sepolia one - and a
            # silent default is how a sepolia key ends up on a mainnet
            # deployment in the first place.
            network=spec.get("network", "mainnet"),
            fee_token_address=spec.get('''

src = open(PATH, encoding="utf-8").read()

if "network=spec.get(\"network\"" in src:
    print("  the network is already passed")
elif OLD in src:
    src = src.replace(OLD, NEW, 1)
    open(PATH, "w", encoding="utf-8").write(src)
    print("  the network is now passed explicitly")
else:
    print("  the pattern was not found")
    sys.exit(1)

try:
    compile(src, PATH, "exec")
    print("  it parses")
except SyntaxError as e:
    print(f"  it does NOT parse: {e}")
    sys.exit(1)
