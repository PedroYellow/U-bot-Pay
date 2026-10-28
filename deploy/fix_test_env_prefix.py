#!/usr/bin/env python3
"""Use the deployment's own env prefix in a test's subprocess env.

The test set the MAIN deployment's variable names:

    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")

but the deployment's api.py asks for ITS OWN prefix:

    "Utron_API_KEY": "server-to-server auth; ..."

So the subprocess started without the variable the module wanted, and
the import failed with:

    RuntimeError: Utron_API_KEY must be set

which reads like a deployment whose secrets were never configured.

The prefix is read from the deployment's .env - DEPLOY_PREFIX - so the
test sets the names that deployment actually uses.

Usage: fix_test_env_prefix.py <test.py> <prefix>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/utronpay/test_bridge_m5_target_validation.py"
prefix = sys.argv[2] if len(sys.argv) > 2 else "UTRON"

src = open(path, encoding="utf-8").read()

OLD = '''    env = os.environ.copy()
    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")'''

NEW = '''    env = os.environ.copy()
    # This deployment's own variable names. The test set the MAIN
    # deployment's - UBOT_API_KEY - but the deployment's api.py asks
    # for its own prefix, so the subprocess started without the
    # variable the module wanted and the import failed with
    # "Utron_API_KEY must be set".
    #
    # The prefix comes from the deployment's .env, so it is right for
    # whichever deployment the test is in.
    env.setdefault(f"{_DEPLOY_PREFIX}_API_KEY", "x")
    env.setdefault(f"{_DEPLOY_PREFIX}_JWT_SECRET", "x")'''

if OLD not in src:
    print("  the env block was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)

# and the prefix itself, read from the env file the loader already read
ANCHOR = 'HERE = os.path.dirname(os.path.abspath(__file__))'
PREFIX_BLOCK = '''HERE = os.path.dirname(os.path.abspath(__file__))

# This deployment's env prefix, from its own .env. The test sets the
# subprocess's variable names from it.
_DEPLOY_PREFIX = os.environ.get("DEPLOY_PREFIX", "''' + prefix + '''").strip().upper()'''

if ANCHOR in src:
    src = src.replace(ANCHOR, PREFIX_BLOCK, 1)
else:
    print("  the HERE anchor was not found")

open(path, "w", encoding="utf-8").write(src)
print(f"{path}: the subprocess env now uses {prefix}_* names")
