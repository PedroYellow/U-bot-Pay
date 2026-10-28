#!/usr/bin/env python3
"""Add the existing-file chmod to a core.py that already has the umask fix.

The umask branch only applies when the database does not exist yet. An
existing database - one created before the fix - stays world-readable
forever, because the umask never runs for it.

This adds the missing chmod after the pragma loop in the fallback
connect path, so the file is tightened on the next connection.

Usage: add_existing_db_chmod.py <core.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/ubotpay/core.py"

OLD = """    c = sqlite3.connect(DB, timeout=_BUSY_TIMEOUT_S)
    c.row_factory = sqlite3.Row
    for p in _PRAGMAS:
        try:
            c.execute(p)
        except sqlite3.DatabaseError:
            pass
    return c"""

NEW = """    c = sqlite3.connect(DB, timeout=_BUSY_TIMEOUT_S)
    c.row_factory = sqlite3.Row
    for p in _PRAGMAS:
        try:
            c.execute(p)
        except sqlite3.DatabaseError:
            pass
    # An existing database may still be world-readable from before the
    # umask fix - the umask only applies when the file is created. Tighten
    # it here, so a database that predates the fix is corrected on the
    # next connection rather than needing a manual chmod.
    try:
        if os.path.exists(DB):
            mode = os.stat(DB).st_mode & 0o777
            if mode & 0o004:
                os.chmod(DB, 0o600)
    except OSError:
        pass
    return c"""

src = open(path, encoding="utf-8").read()
if "os.chmod(DB, 0o600)" in src:
    print("the chmod is already there")
    sys.exit(0)
if OLD not in src:
    print("PATTERN NOT FOUND - needs a manual look")
    for i, line in enumerate(src.split("\n"), 1):
        if "_open_conn" in line or "sqlite3.connect(DB" in line:
            print(f"  {i}: {line}")
    sys.exit(1)

open(path, "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
print("the existing-file chmod was added")
