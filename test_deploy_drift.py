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
#
# Each entry is (repository path, path on the VPS). They differ for
# one file: the runner is deploy/run_tests_portable.sh in the
# repository and run_tests.sh on the server. Listing it as one name
# assumed they were the same, and the drift check reported it as
# missing - a false alarm about the one file it was checking.
FILES = [
    ("api.py", "api.py"),
    ("chain.py", "chain.py"),
    ("deploy/run_tests_portable.sh", "run_tests.sh"),
    ("deploy/deploy_web.sh", "deploy/deploy_web.sh"),
    ("deploy/nginx.conf", "deploy/nginx.conf"),
    ("deploy/add_agent_vhost.sh", "deploy/add_agent_vhost.sh"),
    ("deploy/add_agent_port80.py", "deploy/add_agent_port80.py"),
    ("web/index.html", "web/index.html"),
    ("web/ubot.css", "web/ubot.css"),
    ("web/i18n.js", "web/i18n.js"),
    ("web/sw.js", "web/sw.js"),
    ("web/manifest.json", "web/manifest.json"),
    ("web/security.txt", "web/security.txt"),
    ("web/agent.html", "web/agent.html"),
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


def check(rel, remote_rel=None):
    local = md5_local(os.path.join(REPO, rel))
    if local is None:
        print(f"  FAIL  {rel} is not in the repository ({REPO})")
        failures.append(f"{rel} missing from the repository")
        return
    remote = md5_remote(f"/opt/ubotpay/{remote_rel or rel}")
    if remote is None:
        print(f"  FAIL  {remote_rel or rel} is missing on the VPS")
        failures.append(f"{remote_rel or rel} missing on the VPS")
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
for rel, remote_rel in FILES:
    check(rel, remote_rel)

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
print("D4: the nginx vhosts have one Host header per location")
print("=" * 72)
# A duplicated `proxy_set_header Host` makes nginx send TWO Host
# headers, and uvicorn rejects the request with "Invalid HTTP
# request received". So the endpoint answers 400 to every caller from
# the internet while working perfectly from inside the VPS - which is
# exactly why the drift check above could not see it: both sides of
# that check run inside the server.
#
# So the vhosts are checked for the duplicate as well as for being
# identical to the repository.
host_check = os.path.join(REPO, "deploy", "check_nginx_host_headers.py")
if os.path.exists(host_check):
    r = subprocess.run([sys.executable, host_check,
                         os.path.join(REPO, "deploy", "nginx-vhosts")],
                        capture_output=True, text=True, timeout=120)
    for line in (r.stdout or "").splitlines():
        if line.strip():
            print(f"  {line}")
    if r.returncode != 0:
        failures.append("an nginx vhost has a duplicated Host header")
else:
    print("  the check script is missing")

print()
print("=" * 72)
print("D5: the Starknet engine implements the whole engine interface")
print("=" * 72)
# StarknetEngine inherits from NOTHING, so it does not get
# ChainEngine's methods. One of them was missing and it broke a
# public endpoint: /bridge/chains answered 500 on the Starknet
# deployment with
#
#     AttributeError: 'StarknetEngine' object has no attribute
#     'explorer'. Did you mean: '_explorer'?
#
# while ubotpay and usonicpay answered 200 - because their engines
# inherit from ChainEngine.
#
# So the Starknet engine's public surface is compared with the EVM
# engine's effective surface (the base class plus its own).
iface_check_remote = "/opt/ustartkpay/deploy/ustartkpay/check_starknet_interface.py"
# The SCRIPT is read from the deployment's copy, not from the
# repository's: the repository is on this machine and the deployment
# is on the other one, so a path that exists here does not exist
# there.
#
# It runs ON THE VPS, because the deployment is there. The other
# checks in this file do the same thing - md5_remote, and the nginx
# vhosts - for the same reason: a check that reads the deployment
# has to run where the deployment is.
r = subprocess.run(
    ["ssh", "-o", "BatchMode=yes", SSH,
     f"test -f {iface_check_remote} && "
     f"python3 {iface_check_remote} /opt/ustartkpay"],
    capture_output=True, text=True, timeout=180)
out = r.stdout or ""
for line in out.splitlines():
    if line.strip():
        print(f"  {line}")
if "MISSING" in out:
    failures.append("the Starknet engine is missing interface methods")
elif not out.strip():
    print("  the check did not run - is the deployment there?")
    failures.append("the Starknet interface check did not run")

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
