#!/usr/bin/env python3
"""Import the deployment's env shim before the modules that need it.

Each whitelabel has an _env_shim that maps its own env vars onto the
names the inherited codebase reads:

    UTRON_API_KEY  ->  UBOT_API_KEY, Utron_API_KEY

The shim runs once, before any module-level code reads os.environ.
The service imports it first, in main_utron.py:

    import _env_shim     # noqa: F401  must run first
    import api, tg

A test that imports api directly does NOT get the shim, so api.py
asks for Utron_API_KEY and the environment only has UTRON_API_KEY -
and on Linux the variable names are case-sensitive, so the lookup
finds nothing:

    RuntimeError: Utron_API_KEY must be set

which reads like a deployment whose secrets were never configured.

So the test imports the shim before it imports the module it needs.

Usage: fix_env_shim_import.py <test.py> [<module>]
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/utronpay/test_bridge_m5_target_validation.py"
module = sys.argv[2] if len(sys.argv) > 2 else "api"

src = open(path, encoding="utf-8").read()

SHIM = '''# The env shim maps this deployment's own variable names onto the ones
# the inherited code reads - UTRON_API_KEY becomes Utron_API_KEY and
# UBOT_API_KEY. It has to run BEFORE anything reads os.environ, which
# is why the service imports it first in main_utron.py.
#
# A test that imports the module directly does not get the shim, and
# on Linux the variable names are case-sensitive, so the module asked
# for a variable that was not there.
import _env_shim  # noqa: F401  must run first
'''

if "import _env_shim" in src:
    print(f"{path}: it already imports the shim")
    sys.exit(0)

# right before the first import of the module that needs it
lines = src.split("\n")
insert_at = None
for i, line in enumerate(lines):
    if line.strip().startswith(f"import {module}") or \
       line.strip().startswith(f"from {module}"):
        insert_at = i
        break

if insert_at is None:
    # not a top-level import - put it after the last import at the top
    for i, line in enumerate(lines[:60]):
        if line.startswith("import ") or line.startswith("from "):
            insert_at = i + 1
    insert_at = insert_at or 0

lines.insert(insert_at, SHIM)
out = "\n".join(lines)

try:
    compile(out, path, "exec")
except SyntaxError as e:
    print(f"{path}: the insertion broke it ({e}) - not written")
    sys.exit(1)

open(path, "w", encoding="utf-8").write(out)
print(f"{path}: the env shim is imported at line {insert_at + 1}")
