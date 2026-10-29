#!/usr/bin/env python3
"""Find what the Starknet engine is missing from the ChainEngine interface.

`class StarknetEngine:` does not inherit from `ChainEngine`, so it does
not get the base class's methods. One of them is `explorer()`:

    chains/engine.py:131
        def explorer(self) -> str:
            return getattr(self, "_explorer", "")

and chain.py calls it:

    self.explorer = engine.explorer()

So /bridge/chains answered 500 on the Starknet deployment:

    AttributeError: 'StarknetEngine' object has no attribute 'explorer'.
    Did you mean: '_explorer'?

while ubotpay and usonicpay answered 200 - because their engines DO
inherit from ChainEngine.

This script compares the Starknet engine against the base class's
public surface, so the whole gap is visible at once rather than one
AttributeError at a time.

Usage: check_starknet_interface.py [deployment-dir]
"""
import ast
import os
import sys

DEPLOY = sys.argv[1] if len(sys.argv) > 1 else "/opt/ustartkpay"


def public_methods(path):
    """The public method names defined on the classes in a file."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not item.name.startswith("_"):
                        names.add(item.name)
                elif isinstance(item, ast.Assign):
                    for t in item.targets:
                        if isinstance(t, ast.Name) and not t.id.startswith("_"):
                            names.add(t.id)
    return names


def base_surface(path):
    """The public names of ChainEngine in engine.py."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ChainEngine":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not item.name.startswith("_"):
                        names.add(item.name)
                elif isinstance(item, ast.Assign):
                    for t in item.targets:
                        if isinstance(t, ast.Name) and not t.id.startswith("_"):
                            names.add(t.id)
    return names


def main():
    engine_py = os.path.join(DEPLOY, "chains", "engine.py")
    stark_py = os.path.join(DEPLOY, "chains", "starknet.py")
    evm_py = os.path.join(DEPLOY, "chains", "evm.py")

    for p in (engine_py, stark_py, evm_py):
        if not os.path.exists(p):
            print(f"  missing: {p}")
            return 1

    base = base_surface(engine_py)
    stark = public_methods(stark_py)
    evm = public_methods(evm_py)

    # The EVM engine inherits from ChainEngine, so its effective
    # surface is base + its own. The Starknet engine inherits from
    # NOTHING - it is a plain class - so its surface is only its own.
    evm_effective = base | evm
    stark_effective = stark

    print(f"  the ChainEngine base: {len(base)} public names")
    print(f"  the EVM engine:       {len(evm_effective)} effective")
    print(f"  the Starknet engine:  {len(stark_effective)} effective")
    print()
    missing = sorted(evm_effective - stark_effective)
    if missing:
        print("  MISSING from the Starknet engine:")
        for m in missing:
            print(f"    {m}")
    else:
        print("  nothing missing")

    print()
    print("  and what the Starknet engine has that the EVM one does not:")
    for m in sorted(stark_effective - evm_effective):
        print(f"    {m}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
