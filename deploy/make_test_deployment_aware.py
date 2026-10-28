#!/usr/bin/env python3
"""Make a test file deployment-aware instead of hardcoded to one brand.

The audit-finding tests hardcoded the paths and the service name of the
deployment they were written for:

    sys.path.insert(0, "/opt/usolpay")
    ENV_FILE = "/opt/usolpay/.env"
    ["journalctl", "-u", "usolpay", ...]
    PUBLIC = "https://api.usolpay.com"

In a Sonic deployment every one of those pointed at the Solana service,
so the test checked the wrong deployment's journal and the wrong
database's permissions. It failed for the right reason and the wrong
cause.

This rewrites them to come from the deployment's own prefix.

Usage: make_test_deployment_aware.py <test.py> <old-brand> <new-brand>
  old-brand: usolpay   (what is hardcoded now)
  new-brand: usonicpay (what it should be)
"""
import os
import re
import sys

path = sys.argv[1]
old = sys.argv[2]
new = sys.argv[3]

src = open(path, encoding="utf-8").read()
before = src

# the deployment root and env file
src = src.replace(
    'sys.path.insert(0, os.environ.get("ENGINE_ROOT", "/opt/%s"))' % old,
    'sys.path.insert(0, os.environ.get("ENGINE_ROOT", "/opt/%s"))' % new)
src = src.replace(
    'ENV_FILE = os.environ.get("%sPAY_ENV_FILE", "/opt/%s/.env")' % (old.upper(), old),
    'ENV_FILE = os.environ.get("%sPAY_ENV_FILE", "/opt/%s/.env")' % (new.upper(), new))

# the database path
src = src.replace('"/opt/%s/%s.db"' % (old, old), '"/opt/%s/%s.db"' % (new, new))

# the service name
src = re.sub(r'"%s"' % re.escape(old), '"%s"' % new, src)

# the public hostnames
src = src.replace("https://api.%s.com" % old, "https://api.%s.com" % new)
src = src.replace("https://%s.com" % old, "https://%s.com" % new)

if src != before:
    open(path, "w", encoding="utf-8").write(src)
    print(f"{path}: rewritten for {new}")
else:
    print(f"{path}: nothing to change")

# what is left that still names the old brand?
for i, line in enumerate(src.split("\n"), 1):
    if old in line:
        print(f"    {i}: {line.strip()[:110]}")
