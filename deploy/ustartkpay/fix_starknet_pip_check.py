#!/usr/bin/env python3
"""Fix the inverted pip check in the Starknet deployment script.

The script ran:

    "$VENV/bin/python" - <<'CHECK' || exit 1
    import subprocess, sys
    out = subprocess.run([sys.executable, "-m", "pip", "check"],
                         capture_output=True, text=True).stdout.strip()
    if out:
        print("  pip check:", out)
        sys.exit(1)
    print("  pip check: no conflicts")
    CHECK

The check is inverted, because `pip check` PRINTS on success:

    rc     : 0
    stdout : 'No broken requirements found.\\n'
    stderr : ''

So `out` is never empty - not even when everything is fine - and the
`if out:` takes the success path as a conflict. The script then exits
1 and the deploy FAILS, every time, even with a perfect venv.

Which is what happened: the deploy reported EXIT=1 and EXIT=120, the
output stopped right after `pip show starknet-py`, and the venv was
actually clean the whole time. `pip check` a pelo says:

    No broken requirements found.
    rc=0

The exit code is the signal. `pip check` exits non-zero when there IS
a conflict, and prints the conflict to stdout. On success it exits 0
and prints "No broken requirements found."

So the check becomes:

    r = subprocess.run([sys.executable, "-m", "pip", "check"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("  pip check:", (r.stdout or r.stderr).strip())
        sys.exit(1)
    print("  pip check: no conflicts")

And it keeps the message, because the point of a fatal check is that
the next person sees WHY.

Usage: fix_starknet_pip_check.py <deploy_ustartkpay.sh>
"""
import shutil
import sys

OLD = '''"$VENV/bin/python" - <<'CHECK' || exit 1
import subprocess, sys
out = subprocess.run([sys.executable, "-m", "pip", "check"],
                     capture_output=True, text=True).stdout.strip()
if out:
    print("  pip check:", out)
    sys.exit(1)
print("  pip check: no conflicts")
CHECK'''

NEW = '''"$VENV/bin/python" - <<'CHECK' || exit 1
import subprocess, sys
# The EXIT CODE is the signal, not the output. `pip check` prints
# "No broken requirements found." on success, so a check on whether
# the output is empty treats every clean venv as a conflict - and the
# deploy failed every time, with a perfectly good venv underneath.
r = subprocess.run([sys.executable, "-m", "pip", "check"],
                   capture_output=True, text=True)
if r.returncode != 0:
    print("  pip check:", (r.stdout or r.stderr).strip())
    sys.exit(1)
print("  pip check: no conflicts")
CHECK'''


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "/opt/ustartkpay/deploy/deploy_ustartkpay.sh"

    with open(path, encoding="utf-8") as f:
        src = f.read()

    if "r.returncode != 0" in src:
        print("  the check is already fixed")
        return 0

    if OLD not in src:
        print("  the block was not found")
        return 1

    src = src.replace(OLD, NEW, 1)

    shutil.copy2(path, path + ".bak-pipcheck")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"  {path}: the pip check now reads the exit code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
