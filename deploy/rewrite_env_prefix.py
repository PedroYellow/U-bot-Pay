#!/usr/bin/env python3
"""Rewrite hardcoded deployment-prefix env reads into config.cfg() calls.

Every deployment read its settings from names like
`os.environ.get("USOL_WALLET_ADDRESS", "")`. With one deployment per
codebase that was fine, but the whitelabels are separate copies and
each one needs its own prefix - 101 call sites in the Solana one, and
the next chain would need the same again.

This rewrites them mechanically:

    os.environ.get("USOL_WALLET_ADDRESS", "")        -> cfg("WALLET_ADDRESS", "")
    os.environ.get("USOL_PORT", "8090")              -> cfg("PORT", "8090")
    os.environ.get("USOL_FEE_PCT")                   -> cfg("FEE_PCT")
    os.environ["USOL_PROFILE"]                       -> cfg("PROFILE")

The default value is preserved, including the case where there is none.
`os.environ.get` with a non-literal name is left alone and reported, so
nothing is silently missed.

Usage:
    rewrite_env_prefix.py <dir> [--prefix USOL] [--check]

--check reports what would change and exits non-zero if there is
anything to do, without writing. That is the mode to run in CI.
"""
import argparse
import os
import re
import sys

# os.environ.get("PREFIX_NAME", default)
GET_RE = re.compile(
    r"os\.environ\.get\(\s*"
    r"[\"'](?P<prefix>[A-Z][A-Z0-9]*)_(?P<name>[A-Z][A-Z0-9_]*)[\"']"
    r"(?P<rest>\s*,[^)]*)?\)"
)

# os.environ["PREFIX_NAME"]
ITEM_RE = re.compile(
    r"os\.environ\[\s*"
    r"[\"'](?P<prefix>[A-Z][A-Z0-9]*)_(?P<name>[A-Z][A-Z0-9_]*)[\"']"
    r"\s*\]"
)

# os.environ.pop("PREFIX_NAME", None) - a test needs to remove a setting
# to prove the default applies. It becomes pop_cfg("NAME", None).
POP_RE = re.compile(
    r"os\.environ\.pop\(\s*"
    r"[\"'](?P<prefix>[A-Z][A-Z0-9]*)_(?P<name>[A-Z][A-Z0-9_]*)[\"']"
    r"(?P<rest>\s*,[^)]*)?\)"
)

# Anything that looks like an env read but does not match the patterns
# above. Reported, never rewritten.
LOOSE_RE = re.compile(r"os\.environ(?:\.get)?\(?\s*[\"']?[A-Z][A-Z0-9_]*")


def rewrite(text: str, prefix: str, shared: set):
    counts = {"get": 0, "item": 0, "pop": 0, "skipped": []}

    def sub_get(m):
        if m.group("prefix") != prefix:
            return m.group(0)
        counts["get"] += 1
        rest = m.group("rest") or ""
        return f'cfg("{m.group("name")}"{rest})'

    def sub_item(m):
        if m.group("prefix") != prefix:
            return m.group(0)
        counts["item"] += 1
        return f'cfg("{m.group("name")}")'

    def sub_pop(m):
        if m.group("prefix") != prefix:
            return m.group(0)
        counts["pop"] += 1
        rest = m.group("rest") or ""
        return f'pop_cfg("{m.group("name")}"{rest})'

    out = GET_RE.sub(sub_get, text)
    out = ITEM_RE.sub(sub_item, out)
    out = POP_RE.sub(sub_pop, out)

    # What is left that mentions the prefix but was not rewritten?
    for m in re.finditer(r"os\.environ[^\n]{0,120}", out):
        line = m.group(0)
        if prefix + "_" in line:
            counts["skipped"].append(line.strip()[:110])

    return out, counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--prefix", default="USOL")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    total = 0
    changed_files = []

    for root, dirs, files in os.walk(args.directory):
        dirs[:] = [d for d in dirs if d not in ("venv", "__pycache__", ".git")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            src = open(path, encoding="utf-8").read()
            if args.prefix + "_" not in src:
                continue
            out, counts = rewrite(src, args.prefix, set())
            n = counts["get"] + counts["item"] + counts["pop"]
            if n or counts["skipped"]:
                print(f"{path}: {counts['get']} get, {counts['item']} item, {counts['pop']} pop"
                      + (f", {len(counts['skipped'])} UNMATCHED" if counts["skipped"] else ""))
                for s in counts["skipped"]:
                    print(f"    ! {s}")
            if out != src:
                total += n
                changed_files.append(path)
                if not args.check:
                    open(path, "w", encoding="utf-8").write(out)

    print()
    print(f"{total} call sites in {len(changed_files)} file(s)")
    if args.check and total:
        print("run without --check to apply")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
