#!/usr/bin/env python3
"""Turn cfg("X") = value into set_cfg("X", value).

The env-prefix rewriter converts every `os.environ["X"] = value` into
`cfg("X") = value`, which is a syntax error - a function call cannot be
an assignment target. This rewrites those into set_cfg(), which writes
the prefixed name.

Run it after rewrite_env_prefix.py.
"""
import os
import re
import sys

# cfg("NAME") = something
ASSIGN_RE = re.compile(r"cfg\((\"[A-Z][A-Z0-9_]*\")\)\s*=\s*")
# cfg('NAME') = something
ASSIGN_RE_SQ = re.compile(r"cfg\('([A-Z][A-Z0-9_]*)'\)\s*=\s*")


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    fixed = []
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("venv", "__pycache__", ".git")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            src = open(p, encoding="utf-8").read()
            out = ASSIGN_RE.sub(r'set_cfg(\1, ', src)
            out = ASSIGN_RE_SQ.sub(r"set_cfg('\1', ", out)
            if out != src:
                open(p, "w", encoding="utf-8").write(out)
                fixed.append(p.replace(root.rstrip("/") + "/", ""))
    print(f"fixed {len(fixed)} assignment target(s)")
    for f in fixed:
        print("  ", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
