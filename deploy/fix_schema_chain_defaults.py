#!/usr/bin/env python3
"""Replace the hardcoded chain ids in the schema with the deployment's.

core.py had Solana's chain id hardcoded in eight places:

    CREATE TABLE ... chain_id INTEGER DEFAULT 1151111081099710
    "ALTER TABLE users ADD COLUMN chain_id INTEGER DEFAULT 1151111081099710"

The chain id is a per-deployment value - 43114 on Avalanche, 146 on
Sonic, 728126428 on Tron - and it is already available as
chain.CHAIN_ID. Hardcoding one meant every deployment's schema
default was wrong, and a row inserted without an explicit chain id
landed on the wrong chain.

The bridge module keeps its own chain registry - that is a registry
of the OTHER chains a bridge can reach, and it is correct to name
them there. This only touches the schema and the defaults that mean
"this deployment's chain".

Usage: fix_schema_chain_defaults.py <core.py>
"""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/core.py"

src = open(path, encoding="utf-8").read()
n = 0

# 1. the schema defaults
src, k = re.subn(
    r"(chain_id INTEGER DEFAULT )1151111081099710",
    r"\g<1>{CHAIN_ID_DEFAULT}",
    src)
n += k

# 2. the ALTER TABLE migrations
src, k = re.subn(
    r"(ADD COLUMN chain_id\s+INTEGER DEFAULT )1151111081099710",
    r"\g<1>{CHAIN_ID_DEFAULT}",
    src)
n += k

# 3. the placeholder becomes a module constant, defined from the chain
if "{CHAIN_ID_DEFAULT}" in src:
    anchor = "SCHEMA = \"\"\""
    i = src.find(anchor)
    if i == -1:
        # find the SCHEMA assignment any way
        m = re.search(r'^SCHEMA\s*=', src, re.M)
        i = m.start() if m else 0

    const = '''# This deployment's chain id, for the schema's column defaults.
#
# The defaults were hardcoded to Solana's chain id in every deployment.
# A row inserted without an explicit chain id - which is what
# add_contact did - landed on the wrong chain, and the unique index on
# (user_id, alias, chain_id) made the same alias on the right chain a
# different row.
CHAIN_ID_DEFAULT = chain.CHAIN_ID

'''
    src = src[:i] + const + src[i:]

# 4. the f-string prefix: the schema is an f-string only if it is now
#    needed
if "{CHAIN_ID_DEFAULT}" in src:
    # find the schema assignment and make it an f-string
    m = re.search(r'SCHEMA\s*=\s*(f?)"""', src)
    if m and not m.group(1):
        src = src[:m.start()] + 'SCHEMA = f"""' + src[m.end():]

open(path, "w", encoding="utf-8").write(src)
print(f"{path}: {n} chain id default(s) now come from the deployment")

print()
print("the remaining hardcoded chain ids in this file:")
for i, line in enumerate(src.split("\n"), 1):
    if "1151111081099710" in line:
        print(f"  {i}: {line.strip()[:100]}")
