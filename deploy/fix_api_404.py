#!/usr/bin/env python3
"""Stop the API hosts answering unknown paths with the site's HTML.

Every whitelabel serves its web and its API from ONE server block:

    server_name ubotpay.com www.ubotpay.com api.ubotpay.com;

so the SPA catch-all applies to the API host too:

    location / {
        try_files $uri $uri.html $uri/ /index.html;
    }

The result is that the API host answers 200 with the site's HTML for
anything it does not have a route for:

    https://api.ubotpay.com/.env           -> 200  text/html  (the page)
    https://api.ubotpay.com/server.key     -> 200  text/html  (the page)
    https://api.ubotpay.com/.git/config    -> 200  text/html  (the page)
    https://api.ubotpay.com/nonsense       -> 200  text/html  (the page)

No secret leaks - the SPA fallback serves index.html, not files. But it
is three defects in one:

  - a scanner sees 200 on /.env and /.git/config and keeps digging,
    because a 404 would have told it to stop
  - an API client that hits a wrong path gets HTML where it expected
    JSON, and parses the failure as "the endpoint is broken"
  - the 200 makes the path look like it EXISTS

The fix is a `return 404` on the API host, for the paths the API does
not have a route for.

    location / {
        if ($<map>) { return 404; }
        try_files $uri $uri.html $uri/ /index.html;
    }

`if` with a `return` IS safe - the unsafe part of `if` is anything
that rewrites the request. A `return` inside `if` is the documented
way to do exactly this.

The variable comes from a `map` in the http block, because a map is
GLOBAL - the same rule as the CORS maps, which collided when two
vhosts used the same name.

## Why this walks the file instead of replacing the first match

The first version of this script did a plain string replace of

    location / {
        try_files $uri $uri.html $uri/ /index.html;

and it put the `if` in the wrong server block. These vhosts have
SEVERAL `location /` blocks:

  - one in the main server block (the web + the API)
  - one in the agent.<domain> server block
  - one in the port-80 redirect block

and the first one that matches the text is not necessarily the one
that serves the API. The vhost was left with the `if` in the agent
block, so the API still answered 200 - and the fix looked applied.

So this finds the server block whose `server_name` contains the API
host, and patches the `location /` INSIDE that block.

Usage: fix_api_404.py <vhost.conf> <api-host> <map-name> [nginx.conf]
"""
import os
import re
import shutil
import sys

IF_BLOCK = """        if ($%(map)s) { return 404; }
"""

MAP_BLOCK = """
# ── the API hosts answer 404 for unknown paths ───────────────────
# The web and the API share a server block, so the SPA catch-all
# would answer the site's HTML for anything the API does not have a
# route for - including /.env and /.git/config, which a scanner
# tries on every domain it finds.
#
# A 404 there is both more honest and more useful: it tells the
# scanner the path does not exist, and it gives an API client a
# proper error instead of HTML.
#
# The variable is computed here, in the http block, because a map is
# global - the same rule as the CORS maps, which collided when two
# vhosts used the same name.
map $host $%(map)s {
    default 0;
    %(regex)s 1;
}
"""


def server_blocks(lines):
    """Yield (start, end, lines) for each server block."""
    i = 0
    while i < len(lines):
        if re.match(r"^\s*server\s*\{", lines[i]):
            j, depth = i, 0
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                if depth <= 0:
                    break
                j += 1
            yield i, j, lines[i:j + 1]
            i = j + 1
            continue
        i += 1


def patch_vhost(path, api_host, map_name):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    lines = src.splitlines()

    if map_name in src:
        print(f"  {path}: already patched")
        return False

    target = None
    for start, end, block in server_blocks(lines):
        text = "\n".join(block)
        if f"api.{api_host}" not in text:
            continue
        # it must be the HTTPS block, not the port-80 redirect
        if "listen 80" in text and "listen 443" not in text:
            continue
        target = (start, end, block)
        break

    if not target:
        print(f"  {path}: no server block for api.{api_host} was found")
        return False

    start, end, block = target

    # the location / inside THAT block
    loc = None
    for i, line in enumerate(block):
        if re.match(r"^\s*location\s+/\s*\{", line):
            loc = i
            break

    if loc is None:
        print(f"  {path}: the server block has no location /")
        return False

    # the first line inside it
    insert_at = loc + 1
    block.insert(insert_at, IF_BLOCK % {"map": map_name})

    lines[start:end + 1] = block
    out = "\n".join(lines) + "\n"

    shutil.copy2(path, path + ".bak-api404")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"  {path}: the API host will answer 404 (the location / "
          f"at line {start + loc + 1})")
    return True


def patch_http(path, api_host, map_name):
    with open(path, encoding="utf-8") as f:
        src = f.read()

    if map_name in src:
        print(f"  {path}: the map is already there")
        return False

    regex = "~^api[.]" + re.escape(api_host) + "[.]$"
    block = MAP_BLOCK % {"map": map_name, "regex": regex}

    lines = src.splitlines()
    # after the last existing map block, still inside http
    last_map_end = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*map\s+\$", line):
            depth = 0
            j = i
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                if depth <= 0 and j > i:
                    break
                j += 1
            last_map_end = j + 1

    if last_map_end is None:
        i = next((k for k, l in enumerate(lines) if re.match(r"^\s*http\s*\{", l)), None)
        if i is None:
            print(f"  {path}: the http block was not found")
            return False
        insert_at = i + 1
    else:
        insert_at = last_map_end

    out = "\n".join(lines[:insert_at]) + "\n" + block + "\n" + \
        "\n".join(lines[insert_at:]) + "\n"

    shutil.copy2(path, path + ".bak-api404")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"  {path}: the map was added ({regex})")
    return True


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1

    vhost = sys.argv[1]
    api_host = sys.argv[2]
    map_name = sys.argv[3]
    http_conf = sys.argv[4] if len(sys.argv) > 4 else "/etc/nginx/nginx.conf"

    if patch_vhost(vhost, api_host, map_name):
        patch_http(http_conf, api_host, map_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
