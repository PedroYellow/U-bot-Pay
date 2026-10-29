#!/usr/bin/env python3
"""Give every unbounded subprocess.run in the test suite a timeout.

The pattern

    subprocess.run([...], capture_output=True, text=True, env=env)

with no timeout is a hang waiting to happen. Eleven tests in the main
suite had one.

On a loaded host the import alone is the problem:

    import dataagent   -> 78 seconds
    import api         -> 46 seconds

Every one of those tests spawns a fresh Python that imports the
application. So the suite needs six-plus minutes per test under load,
and with no timeout it is not six-plus minutes, it is FOREVER - and the
runner records the eventual kill as a FAIL with no reason. That is how
test_nonce_db_atomic.py and test_seed_for_idempotent.py came to be
reported as failing when neither of them failed.

The fix

Add `timeout=` to every subprocess.run that lacks one. The timeout is
generous, because the import is genuinely slow, but it EXISTS - so a
stuck subprocess is reported as a timeout instead of hanging.

This is done with the AST rather than with string replacement, so it
only touches real subprocess.run calls and never lands inside a
string literal or a comment.

The one thing it will NOT do is touch a call that already has a
timeout, and it will not touch a `check_call`/`Popen` - only
`subprocess.run`.

Usage: fix_subprocess_timeouts.py <dir-or-file> [...]
"""
import ast
import os
import sys

TIMEOUT = 300


def fix_file(path):
    src = open(path, encoding="utf-8").read()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print(f"  {path}: does not parse ({e})")
        return 0

    # the line numbers where a timeout must be inserted
    inserts = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == "run"):
            continue
        # the call has to be subprocess.run
        if not (isinstance(f.value, ast.Name) and f.value.id == "subprocess"):
            continue
        # already has a timeout?
        if any(kw.arg == "timeout" for kw in node.keywords):
            continue
        inserts.append(node)

    if not inserts:
        return 0

    lines = src.splitlines(keepends=True)

    # insert after the LAST line of the call, with the indent of the
    # OTHER keywords - not of the call. `r = subprocess.run(` sits at
    # 4 spaces and its keywords at 8, so the call's own column would
    # put the new keyword at the wrong depth.
    for node in sorted(inserts, key=lambda n: n.lineno, reverse=True):
        end = node.end_lineno           # 1-based

        # the indent of the other keywords - not of the call.
        # `r = subprocess.run(` sits at 4 spaces and its keywords at 8,
        # so the call's own column would put the new keyword at the
        # wrong depth.
        indent = " " * (node.col_offset + 4)
        for kw in node.keywords:
            if kw.value is not None and kw.value.end_lineno:
                kw_line = lines[kw.value.end_lineno - 1]
                lead = len(kw_line) - len(kw_line.lstrip())
                if lead:
                    indent = " " * lead
                    break

        # The closing paren is usually on its own line, so the keyword
        # goes BEFORE it - not after the call's last line, which would
        # put it outside the parentheses.
        close_idx = end - 1
        while close_idx >= 0 and lines[close_idx].strip() != ")":
            close_idx -= 1
        if close_idx < 0:
            # the call ends on the same line as its last argument;
            # fall back to appending to that line
            body = lines[end - 1]
            stripped = body.rstrip("\n").rstrip()
            if stripped.endswith(")"):
                stripped = stripped[:-1].rstrip()
                comma = "" if stripped.endswith(",") else ","
                lines[end - 1] = (f"{stripped}{comma}\n"
                                   f"{indent}timeout={TIMEOUT},\n"
                                   f"{' ' * (node.col_offset)})\n")
            else:
                continue
            continue

        body = lines[close_idx]
        stripped = body.rstrip("\n").rstrip()
        comma = "" if stripped.endswith(",") else ","
        lines.insert(close_idx, f"{indent}timeout={TIMEOUT}{comma}\n")

    out = "".join(lines)
    try:
        ast.parse(out)
    except SyntaxError as e:
        print(f"  {path}: the result does not parse ({e}) - NOT written")
        return 0

    open(path, "w", encoding="utf-8").write(out)
    return len(inserts)


def main():
    targets = []
    for arg in sys.argv[1:]:
        if os.path.isdir(arg):
            for name in sorted(os.listdir(arg)):
                if name.startswith("test_") and name.endswith(".py"):
                    targets.append(os.path.join(arg, name))
        else:
            targets.append(arg)

    total = 0
    for path in targets:
        if not os.path.exists(path):
            print(f"  {path}: missing")
            continue
        n = fix_file(path)
        if n:
            print(f"  {os.path.basename(path)}: {n} call(s) given a timeout")
            total += n

    print(f"  {total} subprocess.run call(s) fixed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
