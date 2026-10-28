#!/usr/bin/env python3
"""Make a test's subprocesses run in this deployment, with its shim.

Three things were wrong with the subprocesses in the utronpay
deployment.

## They ran the MAIN deployment's interpreter

    ["/opt/ubotpay/venv/bin/python", "-c", code]

The utronpay deployment has its own venv - /opt/utronpay/venv - with
tronpy in it, not web3. The two pin eth-abi differently and cannot
share a venv, which is why the deployment carries a web3_stub.

## They did not load the env shim

Each whitelabel has an _env_shim that maps its own variable names
onto the ones the inherited code reads - UTRON_API_KEY becomes
Utron_API_KEY. The shim runs before anything reads os.environ, which
is why the service imports it first in main_utron.py.

A `python -c` subprocess imports nothing, so the module asked for
Utron_API_KEY when the environment only had UTRON_API_KEY - and on
Linux the names are case-sensitive:

    RuntimeError: Utron_API_KEY must be set

## They set the MAIN deployment's variable names

    env.setdefault("UBOT_API_KEY", "x")

The names the deployment's own api.py asks for.

So: the subprocess runs this deployment's interpreter, imports the shim
first, and gets this deployment's variable names.

Usage: fix_subprocess_deployment.py <test.py> <deploy-dir> [<prefix>]
"""
import os
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/utronpay/test_pending_gc_and_cache.py"
deploy = sys.argv[2] if len(sys.argv) > 2 else "/opt/utronpay"
prefix = sys.argv[3] if len(sys.argv) > 3 else "UTRON"

src = open(path, encoding="utf-8").read()
n = 0

# ── 1. the interpreter ────────────────────────────────────────────
old_py = '"/opt/ubotpay/venv/bin/python"'
new_py = 'os.path.join(HERE, "venv", "bin", "python")'
if old_py in src:
    src = src.replace(old_py, new_py)
    n += 1

# ── 2. the shim, before the code under test ──────────────────────
# The subprocess builds a `full` string with the code. The shim has
# to be the first thing it runs.
shim_line = '"import _env_shim\\n"'

# pattern A: a `full = (` block
if re.search(r'full\s*=\s*\(', src):
    # insert as the first entry of the concatenation
    m = re.search(r'(full\s*=\s*\(\s*\n)', src)
    if m and shim_line not in src:
        src = src[:m.end()] + '        ' + shim_line + '\n' + src[m.end():]
        n += 1

# pattern B: an inline "-c" with the code appended
if 'shim_line' not in src:
    m = re.search(r'("-c",\s*")(.*?)("\s*,)', src, re.S)
    if m:
        src = src[:m.start()] + m.group(1) + "import _env_shim\\n" + m.group(2) + m.group(3) + src[m.end():]
        n += 1

# ── 3. this deployment's variable names ──────────────────────────
old_keys = '''    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")'''
new_keys = '''    # This deployment's own variable names. The test set the MAIN
    # deployment's - UBOT_API_KEY - but the deployment's api.py asks
    # for its own prefix, so the subprocess started without the
    # variable the module wanted.
    env.setdefault(f"{_DEPLOY_PREFIX}_API_KEY", "x")
    env.setdefault(f"{_DEPLOY_PREFIX}_JWT_SECRET", "x")'''
if old_keys in src:
    src = src.replace(old_keys, new_keys, 1)
    n += 1

# ── 4. the prefix, read from the deployment's .env ───────────────
if "_DEPLOY_PREFIX" in src and "HERE = os.path.dirname" in src:
    old_here = "HERE = os.path.dirname(os.path.abspath(__file__))"
    new_here = (old_here + "\n\n"
                "# This deployment's env prefix, from its own .env.\n"
                "_DEPLOY_PREFIX = os.environ.get(\"DEPLOY_PREFIX\", \""
                + prefix + "\").strip().upper()")
    src = src.replace(old_here, new_here, 1)
    n += 1

# ── 5. the .env, loaded before anything reads it ─────────────────
if "ENV_FILE" not in src:
    old_imp = "import sys\n"
    if old_imp in src:
        block = '''import sys

# The environment the service uses. The suite runs code in a
# subprocess, so the deployment's secrets have to be in the environment
# BEFORE the first subprocess starts - api.py refuses to import without
# them.
#
# The file is this deployment's own .env, and the loader uses
# setdefault, so a variable that is already in the environment wins.
ENV_FILE = os.environ.get(
    "''' + prefix + '''PAY_ENV_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
if os.path.exists(ENV_FILE):
    for line in open(ENV_FILE):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip('"').strip("'"))
'''
        src = src.replace(old_imp, block, 1)
        n += 1

open(path, "w", encoding="utf-8").write(src)

try:
    compile(src, path, "exec")
    print(f"{path}: {n} change(s), it parses")
except SyntaxError as e:
    print(f"{path}: {n} change(s) but it does NOT parse: {e}")
    print("  not written")
    sys.exit(1)
