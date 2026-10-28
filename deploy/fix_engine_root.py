#!/usr/bin/env python3
"""Point a test's ENGINE_ROOT at its own deployment.

The whitelabel tests default ENGINE_ROOT to /opt/usolpay - the Solana
deployment they were forked from. In the Sonic deployment that means
the test loads usolpay's registry, usolpay's profile and usolpay's
Solana signer, and then fails on `import base58` because the Sonic
deployment has no Solana SDK.

It looked like a missing dependency. It was actually the test running
against the wrong deployment - and the same test would have PASSED
against usolpay while proving nothing about Sonic.

The default becomes the directory the test file itself lives in, so a
test in a deployment tests that deployment. ENGINE_ROOT still overrides
it, for the case where it is deliberate.

Usage: fix_engine_root.py <test.py> [...]
"""
import os
import re
import sys

OLD = 'sys.path.insert(0, os.environ.get("ENGINE_ROOT", "/opt/usolpay"))'

NEW = '''# The deployment this test belongs to, by default. The default used
# to be /opt/usolpay - the deployment these tests were forked from -
# so in a whitelabel they loaded the WRONG deployment's registry and
# profile and then failed on its missing dependencies. The default is
# now the directory this file lives in, so a test in a deployment tests
# that deployment.
sys.path.insert(0, os.environ.get("ENGINE_ROOT", os.path.dirname(
    os.path.abspath(__file__))))'''

# the same default written the other way
OLD2 = 'sys.path.insert(0, os.environ.get("ENGINE_ROOT", "/opt/usonicpay"))'
NEW2 = '''sys.path.insert(0, os.environ.get("ENGINE_ROOT", os.path.dirname(
    os.path.abspath(__file__))))'''


def main():
    total = 0
    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8").read()
        out = src
        for old, new in ((OLD, NEW), (OLD2, NEW2)):
            if old in out:
                out = out.replace(old, new, 1)
        if out != src:
            open(path, "w", encoding="utf-8").write(out)
            print(f"{path}: the default ENGINE_ROOT is now this file's directory")
            total += 1
        else:
            print(f"{path}: no hardcoded ENGINE_ROOT")
    print(f"{total} file(s) fixed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
