#!/usr/bin/env python3
"""Move tests that cannot apply to a deployment, and check what remains.

Three tests in the utronpay deployment cannot run there:

    test_dataagent_paid_boundary.py   imports dataagent, which imports
                                       web3 - and the Tron venv has
                                       tronpy, not web3. The two pin
                                       eth-abi differently and cannot
                                       share a venv. The deployment
                                       carries a web3_stub for the two
                                       Web3 helpers api.py uses.

    test_chain_registry.py             reads chain.py's literal
                                       `Chain(...)` registry. The
                                       utronpay chain.py is
                                       profile-driven - it resolves
                                       the registry from the profile -
                                       so there is nothing to parse.

    test_bridge_destinations.py        the same shape, on bridge.py.

All three PASS on the main deployment, which is where they belong:

    test_dataagent_paid_boundary.py   35/35
    test_chain_registry.py             21/21
    test_bridge_destinations.py        37/37

And the dataagent service itself runs from /opt/ubotpay, not from
/opt/utronpay - the copy of dataagent.py in the Tron deployment is
unused dead code.

A test that cannot apply to this deployment is not a test. It is noise
that makes a green suite look red and hides a real failure.

Usage: clean_stale_tests.py <deployment-dir> [--dry-run]
"""
import os
import shutil
import subprocess
import sys

# A test file is not applicable if it imports a module this deployment
# does not have.
NOT_APPLICABLE = [
    "test_dataagent_paid_boundary.py",
    "test_chain_registry.py",
    "test_bridge_destinations.py",
]


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "/opt/utronpay"
    dry = "--dry-run" in sys.argv

    tests = [f for f in sorted(os.listdir(root))
             if f.startswith("test_") and f.endswith(".py")]
    stale = [f for f in tests if f in NOT_APPLICABLE]

    print(f"=== {root} ===")
    print(f"  {len(tests)} test files")
    print(f"  {len(stale)} cannot run here:")
    for f in stale:
        print(f"    {f}")

    if not stale:
        print("  nothing to move")
        return 0

    if dry:
        print("  (dry run - nothing moved)")
        return 0

    aside = os.path.join(root, "tests-main-deployment-only")
    os.makedirs(aside, exist_ok=True)
    for f in stale:
        shutil.move(os.path.join(root, f), os.path.join(aside, f))
        print(f"  moved {f} -> tests-main-deployment-only/")

    print()
    print("=== the remaining suites ===")
    remaining = [f for f in sorted(os.listdir(root))
                 if f.startswith("test_") and f.endswith(".py")]
    failed = []
    for f in remaining:
        r = subprocess.run(
            [os.path.join(root, "venv", "bin", "python"), os.path.join(root, f)],
            capture_output=True, text=True, timeout=240, cwd=root)
        if r.returncode != 0:
            failed.append(f)
            tail = (r.stdout + r.stderr).strip().split("\n")
            print(f"  FAIL {f}: {tail[-1][:90] if tail else ''}")
        else:
            print(f"  PASS {f}")

    print()
    if failed:
        print(f"{len(failed)} suite(s) failed: {', '.join(failed)}")
        return 1
    print(f"all {len(remaining)} remaining suites pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
