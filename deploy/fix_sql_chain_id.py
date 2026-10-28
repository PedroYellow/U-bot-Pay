#!/usr/bin/env python3
"""Fix the chain-id replacement inside SQL string literals.

The env-prefix rewrite replaced the hardcoded chain id 1151111081099710
(Solana's) with `chain.CHAIN_ID` everywhere it appeared - including
INSIDE SQL STRING LITERALS:

    c.execute("UPDATE users SET address = ?, chain_id = chain.CHAIN_ID WHERE id = ?", ...)

Python never sees the inside of a string literal, so the replacement was
textual and the SQL shipped with the literal text `chain.CHAIN_ID`
where a number belongs. SQLite read it as a column reference and
answered "no such column: chain.CHAIN_ID", so the statement failed and
the row was left with a NULL chain_id.

The effect: every user, contact, payment and invoice created without an
explicit chain_id got NULL instead of 146. The service did not crash -
the statements are wrapped in try/except - so it looked fine and the
data was quietly wrong.

The fix is a PARAMETER, not string interpolation: the value is the
engine's chain id at runtime and the SQL stays a constant.

Only statements that ACTUALLY contained the literal are touched. A
statement that already used a placeholder is left alone - including
ones where `chain.CHAIN_ID` appears in the parameter tuple rather than
in the SQL, which is correct and must not be rewritten.

Usage: fix_sql_chain_id.py <core.py> [<api.py>]
"""
import re
import sys


def find_sql_strings(line):
    """The quoted fragments of a line that look like SQL."""
    return re.findall(r'"([^"]*)"', line) + re.findall(r"'([^']*)'", line)


def fix(src):
    lines = src.split("\n")
    # the lines whose SQL contained the literal
    touched = set()

    # 1. the placeholders
    for i, line in enumerate(lines):
        if "chain.CHAIN_ID" not in line:
            continue
        for sql in find_sql_strings(line):
            if re.search(r"\bchain_id\s*=\s*chain\.CHAIN_ID\b", sql):
                lines[i] = line.replace("chain_id = chain.CHAIN_ID", "chain_id = ?")
                touched.add(i)
                break

    # 2. the parameters
    for i in sorted(touched):
        sql = None
        for s in find_sql_strings(lines[i]):
            if "chain_id = ?" in s:
                sql = s
                break
        if sql is None:
            continue

        pos = sql.find("chain_id = ?")
        before = sql[:pos].count("?")
        n_placeholders = sql.count("?")

        # the parameter tuple is on the same line or the next one
        for j in (i, i + 1, i + 2):
            if j >= len(lines):
                break
            nxt = lines[j]
            if "chain.CHAIN_ID" in nxt:
                break

            # the tuple is the LAST parenthesised group on the line, and
            # it must not be the execute( call itself
            for m in reversed(list(re.finditer(r"\(([^()]*)\)", nxt))):
                args = m.group(1)
                if "execute(" in nxt[:m.start()]:
                    continue
                parts = [a.strip() for a in args.split(",")] if args.strip() else []
                if len(parts) >= n_placeholders:
                    break
                if len(parts) == 0:
                    parts = ["chain.CHAIN_ID"]
                else:
                    parts.insert(before, "chain.CHAIN_ID")
                lines[j] = nxt[:m.start()] + f"({', '.join(parts)})" + nxt[m.end():]
                break
            else:
                continue
            break

    return "\n".join(lines), len(touched)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/core.py"
    extra = sys.argv[2:]

    total = 0
    for p in [path] + extra:
        src = open(p, encoding="utf-8").read()
        out, n = fix(src)
        if n:
            open(p, "w", encoding="utf-8").write(out)
            print(f"{p}: {n} SQL statement(s) fixed")
            total += n
        else:
            print(f"{p}: no SQL literals to fix")

    print(f"{total} statement(s) fixed")

    # show the result so it can be read
    for p in [path] + extra:
        lines = open(p, encoding="utf-8").read().split("\n")
        for i, line in enumerate(lines):
            for sql in find_sql_strings(line):
                if "chain_id = ?" in sql:
                    nxt = lines[i + 1] if i + 1 < len(lines) else ""
                    print(f"  {p}:{i+1}: {line.strip()[:105]}")
                    if "chain.CHAIN_ID" in nxt:
                        print(f"  {p}:{i+2}: {nxt.strip()[:105]}")
                    break


if __name__ == "__main__":
    main()
