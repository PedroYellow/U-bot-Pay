#!/usr/bin/env python3
"""Restore a test file and insert the env loader at a safe place.

The first attempt at this inserted the loader in the middle of a
multi-line call - the file's import section has several of them - and
the file stopped parsing:

    File "test_usolpay_e2e.py", line 57
        if os.path.exists(ENV_FILE):
    SyntaxError: invalid syntax

The insertion point has to be a line that is not inside a call. The
safe place is after the LAST top-level statement that is not an
open call - which is found by tracking the parenthesis depth.

Usage: fix_env_loader.py <test.py> [...]
"""
import sys

MARKER = "# The environment the service uses."

BLOCK = '''# The environment the service uses. The suite must run with the same
# secrets, because api.py refuses to import without them.
#
# The file is this deployment's own .env, and the loader uses
# setdefault, so a variable that is already in the environment wins.
ENV_FILE = os.environ.get(
    "USOLPAY_ENV_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
if os.path.exists(ENV_FILE):
    for line in open(ENV_FILE):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip('"').strip("'"))
'''


def insertion_point(lines):
    """After the last top-level statement that is complete."""
    depth = 0
    last = 0
    in_doc = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            # a docstring - skip to its end
            q = stripped[:3]
            if not (stripped.endswith(q) and len(stripped) > 5):
                for j in range(i + 1, len(lines)):
                    if q in lines[j]:
                        last = j + 1
                        break
            continue
        depth += line.count("(") - line.count(")")
        depth += line.count("[") - line.count("]")
        depth += line.count("{") - line.count("}")
        if depth <= 0 and stripped and not stripped.startswith("#"):
            last = i + 1
    return last


def main():
    total = 0
    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8").read()
        if MARKER in src:
            print(f"{path}: it already loads the env")
            continue

        lines = src.split("\n")
        at = insertion_point(lines)

        # keep the blank lines tidy
        while at < len(lines) and lines[at].strip() == "":
            at += 1

        new = lines[:at] + [""] + BLOCK.strip("\n").split("\n") + [""] + lines[at:]
        out = "\n".join(new)

        # it has to parse
        try:
            compile(out, path, "exec")
        except SyntaxError as e:
            print(f"{path}: the insertion broke the file ({e}) - not written")
            continue

        open(path, "w", encoding="utf-8").write(out)
        print(f"{path}: the env file is now loaded at line {at + 1}")
        total += 1
    print(f"{total} file(s) fixed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
