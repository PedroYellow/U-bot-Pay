#!/usr/bin/env python3
"""Make the database 0600 after it is created.

SQLite creates the file with the process umask, which under systemd's
default gives 0644 - world-readable. The database holds the treasury
address, the user table, the JWT nonces and every payment. It is not a
public file.

The main U-bot Pay service has the same problem: /opt/ubotpay/ubotpay.db
is 644.

This tightens it once, after the schema is created, and does it in the
code rather than as a deploy-time chmod - a deploy that recreates the
database would otherwise leave it world-readable again.

The umask is set for the process rather than chmod-ing the file, so the
file is never briefly world-readable between creation and the chmod.

Usage: fix_db_permissions.py <core.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/core.py"

OLD = '''def _open_conn():
    c = sqlite3.connect(DB, timeout=_BUSY_TIMEOUT_S)
    c.row_factory = sqlite3.Row
    for p in _PRAGMAS:
        try:
            c.execute(p)
        except sqlite3.DatabaseError:
            pass
    # An existing database may still be world-readable from before this
    # was fixed. Tighten it - a deploy that recreates the database would
    # otherwise leave it world-readable again.
    try:
        if os.path.exists(DB):
            mode = os.stat(DB).st_mode & 0o777
            if mode & 0o004:
                os.chmod(DB, 0o600)
    except OSError:
        pass
    return c'''

NEW = '''def _open_conn():
    # SQLite creates the file with the process umask, which under
    # systemd's default is 0644 - world-readable. The database holds the
    # user table, the JWT nonces and every payment, so it is tightened
    # before the first connection creates it.
    #
    # The umask is used rather than a chmod afterwards, so the file is
    # never briefly world-readable between creation and the fix.
    try:
        _db_dir = os.path.dirname(DB)
        if _db_dir:
            os.makedirs(_db_dir, exist_ok=True)
        if not os.path.exists(DB):
            _old_umask = os.umask(0o077)
            try:
                c = sqlite3.connect(DB, timeout=_BUSY_TIMEOUT_S)
            finally:
                os.umask(_old_umask)
            c.row_factory = sqlite3.Row
            for p in _PRAGMAS:
                try:
                    c.execute(p)
                except sqlite3.DatabaseError:
                    pass
            return c
    except (OSError, sqlite3.Error):
        # Fall through to the plain connect. A permission problem here
        # must not stop the service from booting - the read paths are
        # useful, and the error is loud enough when a write fails.
        pass

    c = sqlite3.connect(DB, timeout=_BUSY_TIMEOUT_S)'''

src = open(path, encoding="utf-8").read()
if OLD in src:
    open(path, "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
    print("the database is now created 0600")
elif "_old_umask" in src:
    print("already fixed")
else:
    print("PATTERN NOT FOUND - needs a manual look")
    sys.exit(1)
