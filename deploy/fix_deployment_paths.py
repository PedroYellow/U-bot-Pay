#!/usr/bin/env python3
"""Point a test at the deployment it belongs to.

Three of the usolpay tests pointed at a LOCAL development path:

    sys.path.insert(0, os.environ.get("ENGINE_ROOT", "/data/workspace/ubotpay_tron"))
    PROFILE_PATH = os.environ.get("PROFILE_PATH", "/data/workspace/ubotpay_tron/profiles/usolpay.yaml")

That path exists on a developer's machine and nowhere else. On the
server the tests failed with:

    FileNotFoundError: No such file or directory:
    '/data/workspace/ubotpay_tron/profiles/usolpay.yaml'

which reads like a missing profile rather than a path pointing at the
wrong machine.

The default is now the directory the test file lives in, so a test in
a deployment tests that deployment. The env vars still override it,
for the case where pointing elsewhere is deliberate.

Usage: fix_deployment_paths.py <test.py> [...]
"""
import os
import re
import sys

# The local dev path that only exists on a workstation.
DEV_ROOT = "/data/workspace/ubotpay_tron"

REPLACEMENTS = [
    # the sys.path insert
    (re.compile(r'os\.environ\.get\(\s*"ENGINE_ROOT"\s*,\s*"'
                + re.escape(DEV_ROOT) + r'"\s*\)'),
     'os.environ.get(\n        "ENGINE_ROOT", os.path.dirname(os.path.abspath(__file__)))'),
    (re.compile(r'os\.environ\.get\(\s*"ENGINE_ROOT"\s*,\s*"'
                + re.escape(DEV_ROOT) + r'"\s*\)'),
     'os.environ.get(\n        "ENGINE_ROOT", os.path.dirname(os.path.abspath(__file__)))'),
    # the profile path
    (re.compile(r'os\.environ\.get\(\s*\n?\s*"PROFILE_PATH"\s*,\s*"'
                + re.escape(DEV_ROOT) + r'/profiles/usolpay\.yaml"\s*\n?\s*\)'),
     'os.environ.get(\n        "PROFILE_PATH",\n        os.path.join(os.path.dirname(os.path.abspath(__file__)),\n                     "profiles", "usolpay.yaml"))'),
    (re.compile(r'os\.environ\.get\(\s*"PROFILE_PATH"\s*,\s*"'
                + re.escape(DEV_ROOT) + r'/profiles/usolpay\.yaml"\s*\)'),
     'os.environ.get(\n        "PROFILE_PATH",\n        os.path.join(os.path.dirname(os.path.abspath(__file__)),\n                     "profiles", "usolpay.yaml"))'),
    # the treasury generator path
    (re.compile(r'os\.environ\.get\(\s*\n?\s*"GEN_PATH"\s*,\s*"'
                + re.escape(DEV_ROOT) + r'/deploy/make_treasury_solana\.py"\s*\n?\s*\)'),
     'os.environ.get(\n        "GEN_PATH",\n        os.path.join(os.path.dirname(os.path.abspath(__file__)),\n                     "deploy", "make_treasury_solana.py"))'),
    # the bare root, in a conditional expression
    (re.compile(r'else\s+"' + re.escape(DEV_ROOT) + r'"'),
     'else os.path.dirname(os.path.abspath(__file__))'),
    (re.compile(r'else\s+"' + re.escape(DEV_ROOT) + r'/profiles/usolpay\.yaml"'),
     'else os.path.join(os.path.dirname(os.path.abspath(__file__)),\n'
     '                                "profiles", "usolpay.yaml")'),
]


def main():
    total = 0
    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8").read()
        out = src
        n = 0
        for pat, rep in REPLACEMENTS:
            out, k = pat.subn(rep, out)
            n += k

        # anything left?
        left = out.count(DEV_ROOT)
        if left:
            print(f"{path}: {left} reference(s) to {DEV_ROOT} remain - "
                  "they need a hand")

        if out != src:
            open(path, "w", encoding="utf-8").write(out)
            print(f"{path}: {n} path(s) now point at this deployment")
            total += 1
        else:
            print(f"{path}: nothing to change")
    print(f"{total} file(s) fixed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
