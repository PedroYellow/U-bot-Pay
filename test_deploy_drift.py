#!/usr/bin/env python3
"""Check that the VPS deployment matches the repository.

The VPS is not a git checkout. Files are copied in, so nothing enforces
that what is running is what was committed. That is not hypothetical:
`deploy/deploy_web.sh` on the VPS was still the pre-audit version for
weeks - it had no backup-archiving, no complete asset manifest and no
credential scan, while the repository had all three. A deploy run from
that copy would have silently skipped the security checks.

The check
----------
For every file the deployment is responsible for, compare the md5 in
the repository against the md5 on the VPS. Anything that differs is
reported.

    deploy/deploy_web.sh          the deploy path itself
    api.py, chain.py              the service
    web/*                         what gets served
    test_*.py                     the suites that run on the VPS

Usage:
    python3 test_deploy_drift.py

The test runs FROM the repository (the repository is not on the VPS) and
reaches out over ssh to compare. Set UBOT_REPO to point it elsewhere and
UBOT_SSH_HOST to change the VPS alias.

It is not part of run_tests.sh: that runner executes on the VPS, where
the repository does not exist, so there would be nothing to compare
against. Run it from the development machine after any deploy.
"""
import hashlib
import os
import subprocess
import sys

REPO = os.environ.get("UBOT_REPO", "/data/workspace/ubotpay")
SSH = os.environ.get("UBOT_SSH_HOST", "hvps")

# Files the deployment owns. The web assets are listed explicitly
# because the site is assembled from them, and a missing one falls back
# to the SPA catch-all and reads as a 200 to anyone checking.
FILES = [
    "api.py",
    "chain.py",
    "run_tests.sh",
    "deploy/deploy_web.sh",
    "deploy/nginx.conf",
    "deploy/add_agent_vhost.sh",
    "deploy/add_agent_port80.py",
    "web/index.html",
    "web/ubot.css",
    "web/i18n.js",
    "web/sw.js",
    "web/manifest.json",
    "web/security.txt",
    "web/agent.html",
]

# The nginx vhosts are compared against the live server, because they
# are edited in place there and are the one place where a stale file
# silently changes behaviour.
VHOSTS = ["ubotpay", "usonicpay", "ustartkpay", "utronpay", "usolpay"]

failures = []
drifted = []


def md5_local(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()
    except FileNotFoundError:
        return None


def md5_remote(path):
    r = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", SSH, f"md5sum {path} 2>/dev/null | cut -d' ' -f1"],
        capture_output=True, text=True, timeout=45,
    )
    out = r.stdout.strip()
    return out if out else None


def check(rel):
    local = md5_local(os.path.join(REPO, rel))
    if local is None:
        print(f"  FAIL  {rel} is not in the repository ({REPO})")
        failures.append(f"{rel} missing from the repository")
        return
    remote = md5_remote(f"/opt/ubotpay/{rel}")
    if remote is None:
        print(f"  FAIL  {rel} is missing on the VPS")
        failures.append(f"{rel} missing on the VPS")
        drifted.append(rel)
    elif remote != local:
        print(f"  FAIL  {rel} differs (repo {local[:8]} / vps {remote[:8]})")
        failures.append(f"{rel} differs from the repository")
        drifted.append(rel)
    else:
        print(f"  PASS  {rel}")


print("=" * 72)
print("D1: the deployed files match the repository")
print("=" * 72)
for rel in FILES:
    check(rel)

print()
print("=" * 72)
print("D2: the nginx vhosts match the repository")
print("=" * 72)
for v in VHOSTS:
    local = md5_local(os.path.join(REPO, "deploy/nginx-vhosts", f"{v}.conf"))
    if local is None:
        print(f"  FAIL  {v} vhost is not in the repository")
        failures.append(f"{v} vhost missing from the repository")
        continue
    remote = md5_remote(f"/etc/nginx/sites-available/{v}")
    if remote is None:
        print(f"  FAIL  {v} vhost is missing on the VPS")
        failures.append(f"{v} vhost missing on the VPS")
        drifted.append(f"nginx:{v}")
    elif remote != local:
        print(f"  FAIL  {v} vhost differs (repo {local[:8]} / vps {remote[:8]})")
        failures.append(f"{v} vhost differs from the repository")
        drifted.append(f"nginx:{v}")
    else:
        print(f"  PASS  {v}")

print()
print("=" * 72)
print("D3: the live web roots match the repository")
print("=" * 72)
for site, rel in [
    ("ubotpay", "web/index.html"),
    ("usonicpay", "web/usonicpay-index.html"),
    ("ustartkpay", "web/ustartkpay-index.html"),
]:
    local = md5_local(os.path.join(REPO, rel))
    if local is None:
        print(f"  FAIL  {site} index.html is not in the repository")
        failures.append(f"{site} index.html missing from the repository")
        continue
    remote = md5_remote(f"/var/www/{site}/index.html")
    if remote is None:
        print(f"  FAIL  {site} index.html is missing")
        failures.append(f"{site} index.html missing")
    elif remote != local:
        print(f"  FAIL  {site} index.html differs (repo {local[:8]} / vps {remote[:8]})")
        failures.append(f"{site} index.html differs from the repository")
        drifted.append(f"web:{site}")
    else:
        print(f"  PASS  {site}")

print()
print("=" * 72)
print(f"{len(failures)} failure(s)")
if drifted:
    print("drifted:")
    for d in drifted:
        print(f"  - {d}")
print("=" * 72)

# Exit non-zero so the suite reports it. A drifted deployment is a real
# problem, not a warning.
sys.exit(1 if failures else 0)
