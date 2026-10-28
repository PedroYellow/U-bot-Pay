#!/usr/bin/env python3
"""Repair every `set_cfg("X", VALUE` that is missing its closing paren.

The env-prefix rewrite turned

    os.environ["X"] = VALUE

into

    cfg("X") = VALUE

and the follow-up pass turned that into

    set_cfg("X", VALUE

with the bracket's `]` consumed and no closing paren. Any assignment
whose value is a plain expression is affected; a value that spans
multiple lines makes it harder to spot by eye.

This finds every `set_cfg(` whose parentheses do not balance on that
line and closes it.

Idempotent.
"""
import os
import re
import sys

SET_CFG_RE = re.compile(r"set_cfg\(\s*(?:\"[A-Z][A-Z0-9_]*\"|'[A-Z][A-Z0-9_]*')\s*,\s*")

# os.environ["X"] = VALUE, still not rewritten
RAW_ASSIGN_RE = re.compile(r"os\.environ\[\s*[\"'](?P<p>[A-Z][A-Z0-9_]*)[\"']\s*\]\s*=\s*")


def balanced(line: str, start: int) -> bool:
    """Is the paren opened at `start` closed on the same line?"""
    depth = 0
    in_str = None
    i = start
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "\"'":
            in_str = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return True
        i += 1
    return False


def fix_file(path: str) -> int:
    src = open(path, encoding="utf-8").read()
    lines = src.split("\n")
    out = []
    fixed = 0

    for line in lines:
        # a raw os.environ["X"] = ... that the rewriter missed
        if RAW_ASSIGN_RE.search(line):
            line = RAW_ASSIGN_RE.sub(
                lambda m: f'set_cfg("{m.group("p")}", ', line)

        # a set_cfg( whose paren never closes on this line
        for m in SET_CFG_RE.finditer(line):
            paren = line.index("(", m.start())
            if not balanced(line, paren):
                line = line.rstrip() + ")"
                fixed += 1
                break

        out.append(line)

    if fixed:
        open(path, "w", encoding="utf-8").write("\n".join(out))
    return fixed


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    total = 0
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("venv", "__pycache__", ".git")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            n = fix_file(p)
            if n:
                print(f"{p}: fixed {n}")
                total += n
    print(f"{total} line(s) repaired")
    return 0


if __name__ == "__main__":
    sys.exit(main())
