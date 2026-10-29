#!/usr/bin/env python3
"""Give the wallet-ratelimit test its own database, and use it.

Two bugs, and the second one is the dangerous one.

## The database path was FIXED

    env.setdefault(f"{_DEPLOY_PREFIX}_DB", "/tmp/test_wallet_rl.db")

Every run of this test used the SAME database file. Two concurrent
runs - which is what happens when the suite is run while a single
test is being checked - raced on it, and one of them failed with an
error that looked like a product defect.

The other tests in this deployment make a fresh temp directory. This
one did not.

## setdefault meant the WRONG database could win

`setdefault` does not set the variable if it is ALREADY set. So if
UTRON_DB was in the environment - from a previous run, from the
service's own environment, from an operator's shell - the test would
use THAT database instead of the temporary one.

In a test that means it writes to the deployment's real database. The
test is called a WALLET RATELIMIT test, so it creates users and
records rate-limit state against whatever database it is pointed at.

So the value is assigned, not defaulted, and it is a fresh temporary
file that is removed afterwards.

Usage: fix_wallet_test_db.py <test.py>
"""
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/opt/utronpay/test_wallet_ratelimit.py"

src = open(PATH, encoding="utf-8").read()

OLD = '''    env.setdefault(f"{_DEPLOY_PREFIX}_DB", "/tmp/test_wallet_rl.db")'''

NEW = '''    # The test's OWN database, in a fresh temp directory.
    #
    # The path used to be a fixed one - /tmp/test_wallet_rl.db - so
    # two concurrent runs raced on it and one failed with an error
    # that looked like a product defect.
    #
    # And it was set with setdefault, which does NOT set a variable
    # that is already set: if UTRON_DB was in the environment, the
    # test would write to THAT database - the deployment's real one.
    # So it is assigned, not defaulted.
    _db_dir = tempfile.mkdtemp(prefix="wallet_rl_")
    _db_path = os.path.join(_db_dir, "test.db")
    env[f"{_DEPLOY_PREFIX}_DB"] = _db_path'''

if OLD not in src:
    print("  the db line was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)

# tempfile has to be imported
if "\nimport tempfile" not in src:
    src = src.replace("import os\n", "import os\nimport tempfile\n", 1)

open(PATH, "w", encoding="utf-8").write(src)

try:
    compile(src, PATH, "exec")
    print(f"{PATH}: the test has its own database, and it is assigned")
except SyntaxError as e:
    print(f"{PATH}: it does NOT parse: {e}")
    sys.exit(1)
