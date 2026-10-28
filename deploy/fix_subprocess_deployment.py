#!/usr/bin/env python3
"""Make a test's subprocess run in this deployment, with its shim.

Two things were wrong with the subprocess.

## It ran the MAIN deployment's interpreter

    ["/opt/ubotpay/venv/bin/python", "-c", code]

The utronpay deployment has its own venv - /opt/utronpay/venv - and
its own dependencies (tronpy, not web3). Running the main deployment's
interpreter against the utronpay tree is running one deployment's code
under another deployment's libraries.

## It did not load the env shim

Each whitelabel has an _env_shim that maps its own variable names
onto the ones the inherited code reads - UTRON_API_KEY becomes
Utron_API_KEY. The shim runs before anything reads os.environ, which
is why the service imports it first in main_utron.py.

A `python -c` subprocess imports nothing, so the module asked for
Utron_API_KEY and the environment only had UTRON_API_KEY - and on
Linux the names are case-sensitive, so the lookup found nothing:

    RuntimeError: Utron_API_KEY must be set

So the subprocess imports the shim before the code under test.

Usage: fix_subprocess_deployment.py <test.py> <deploy-dir>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/utronpay/test_bridge_m5_target_validation.py"
deploy = sys.argv[2] if len(sys.argv) > 2 else "/opt/utronpay"

src = open(path, encoding="utf-8").read()
n = 0

# 1. the interpreter: this deployment's own venv
OLD_PY = '''        ["/opt/ubotpay/venv/bin/python", "-c", code],'''
NEW_PY = '''        # This deployment's own interpreter. The test ran the MAIN
        # deployment's venv against this deployment's tree - one
        # deployment's code under another deployment's libraries.
        [os.path.join(HERE, "venv", "bin", "python"), "-c", code],'''

if OLD_PY in src:
    src = src.replace(OLD_PY, NEW_PY, 1)
    n += 1
else:
    print("  the interpreter line was not found")

# 2. the shim, before the code under test
OLD_RUN = '''    r = subprocess.run(
        [os.path.join(HERE, "venv", "bin", "python"), "-c", code],'''
NEW_RUN = '''    # The env shim has to run before anything reads os.environ. The
    # service imports it first in main_utron.py; a `python -c`
    # subprocess imports nothing, so the module asked for a variable
    # that was not there under the name it wanted.
    if "import _env_shim" not in code:
        code = "import _env_shim\\n" + code

    r = subprocess.run(
        [os.path.join(HERE, "venv", "bin", "python"), "-c", code],'''

if OLD_RUN in src:
    src = src.replace(OLD_RUN, NEW_RUN, 1)
    n += 1
else:
    print("  the subprocess call was not found")

open(path, "w", encoding="utf-8").write(src)
print(f"{path}: {n} change(s)")
