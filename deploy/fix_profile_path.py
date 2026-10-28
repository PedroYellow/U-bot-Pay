#!/usr/bin/env python3
"""Point a test's PROFILE_PATH at its own deployment's profile.

The whitelabel tests default PROFILE_PATH to
/opt/usolpay/profiles/usolpay.yaml - the Solana deployment they were
forked from. In a whitelabel that means the test loads the Solana
profile, asks for a Solana engine, and fails.

It looked like a missing engine. It was the test pointing at another
deployment's profile.

The default becomes the deployment's own profile, derived from the
directory the test file lives in, so a test in a deployment tests that
deployment. PROFILE_PATH still overrides it.

Usage: fix_profile_path.py <test.py> [...]
"""
import os
import re
import sys


def fix(path):
    src = open(path, encoding="utf-8").read()
    out = src

    # the two-line form
    old = '''PROFILE = os.environ.get(
    "PROFILE_PATH", "/opt/usolpay/profiles/usolpay.yaml")'''
    new = '''# This deployment's own profile. The default used to be the Solana
# deployment's profile, so in a whitelabel the test loaded a Solana
# profile and asked for a Solana engine.
_PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")
_PROFILE_NAME = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.environ.get(
    "PROFILE_PATH",
    os.path.join(_PROFILE_DIR, _PROFILE_NAME + ".yaml"))'''

    # the one-line form
    old2 = 'PROFILE = os.environ.get("PROFILE_PATH", "/opt/usolpay/profiles/usolpay.yaml")'
    new2 = new.replace("PROFILE = os.environ.get(", "PROFILE = os.environ.get(", 1)

    if old in out:
        out = out.replace(old, new, 1)
        return out, True
    if old2 in out:
        out = out.replace(old2, new2, 1)
        return out, True

    # any other hardcoded usolpay profile path
    m = re.search(r'os\.environ\.get\(\s*"PROFILE_PATH",\s*"([^"]*)"',
                  out, re.S)
    if m and "usolpay" in m.group(1):
        out = out[:m.start()] + new.split("PROFILE = os.environ.get(", 1)[1]
        out = out[:m.start()] + "PROFILE = os.environ.get(" + out[m.start():]
        return out, True

    return out, False


def main():
    total = 0
    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8").read()
        out, changed = fix(path)
        if changed:
            open(path, "w", encoding="utf-8").write(out)
            print(f"{path}: the default PROFILE_PATH is now this deployment's profile")
            total += 1
        else:
            print(f"{path}: no hardcoded PROFILE_PATH")
    print(f"{total} file(s) fixed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
