#!/usr/bin/env python3
"""Remove the Solana tests from a deployment that is not Solana.

The whitelabels were forked from the Solana deployment, which carried
its tests with it. When the Solana engine was removed - Sonic is EVM,
so it uses the shared `evm` engine - the TESTS were left behind.

Seven of the fifteen test files in the Sonic deployment could not run:
they import solders, base58 or chains.solana_signer, all of which are
gone. They failed with ModuleNotFoundError, which reads like a broken
deployment rather than a stale test.

A test that cannot apply to this deployment is not a test - it is
noise that makes a green suite look red and hides a real failure.

This removes them, and checks that what remains actually runs.

Usage:
    clean_stale_tests.py <deployment-dir> [--dry-run]
"""
import os
import shutil
import subprocess
import sys

# A test file is Solana-specific if it imports any of these.
SOLANA_MARKERS = [
    "solders",
    "base58",
    "solana_signer",
    "chains.solana",
    "spl_instructions",
    "solana_engine",
    "ata_derivation",
]


def is_solana_test(path):
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return False
    return any(m in src for m in SOLANA_MARKERS)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay"
    dry = "--dry-run" in sys.argv

    tests = [f for f in sorted(os.listdir(root))
             if f.startswith("test_") and f.endswith(".py")]
    stale = [f for f in tests
             if is_solana_test(os.path.join(root, f))]

    print(f"=== {root} ===")
    print(f"  {len(tests)} test files")
    print(f"  {len(stale)} are Solana-specific and cannot run here:")
    for f in stale:
        print(f"    {f}")

    if not stale:
        print("  nothing to remove")
        return 0

    if dry:
        print("  (dry run - nothing removed)")
        return 0

    # move them aside rather than deleting: they are the Solana
    # deployment's tests and belong there.
    aside = os.path.join(root, "tests-solana-not-applicable")
    os.makedirs(aside, exist_ok=True)
    for f in stale:
        shutil.move(os.path.join(root, f), os.path.join(aside, f))
        print(f"  moved {f} -> tests-solana-not-applicable/")

    print()
    print("=== the remaining suites ===")
    remaining = [f for f in sorted(os.listdir(root))
                 if f.startswith("test_") and f.endswith(".py")]
    failed = []
    for f in remaining:
        print(f"  {f}")
        r = subprocess.run(["/opt/ubotpay/venv/bin/python", os.path.join(root, f)],
                           capture_output=True, text=True, timeout=240,
                           cwd=root)
        if r.returncode != 0:
            failed.append(f)
            tail = (r.stdout + r.stderr).strip().split("\n")
            print(f"    FAIL: {tail[-1][:110] if tail else ''}")
        else:
            print("    PASS")

    print()
    if failed:
        print(f"{len(failed)} suite(s) failed: {', '.join(failed)}")
        return 1
    print(f"all {len(remaining)} remaining suites pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
