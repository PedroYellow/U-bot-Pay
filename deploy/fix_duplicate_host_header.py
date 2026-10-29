#!/usr/bin/env python3
"""Remove the duplicated `proxy_set_header Host` in the nginx vhosts.

The bug

    location = /bridge/quote {
        proxy_pass http://127.0.0.1:8090/bridge/quote; proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Host $host;          <- <- the second one
        proxy_hide_header Access-Control-Allow-Origin;
    }

Two `proxy_set_header Host` lines in one block means nginx sends TWO
Host headers to the backend. uvicorn rejects a request with two Host
headers:

    HTTP/1.1 400 Bad Request
    Invalid HTTP request received.

Reproduced directly against the app:

    with TWO Host headers:  HTTP/1.1 400 Bad Request
    with ONE Host header:   HTTP/1.1 200 OK

So /bridge/quote returned 400 to EVERY caller from the internet, on
all three whitelabel domains, while working perfectly from inside
the VPS. The front-end calls it for every cross-chain bridge, so the
feature was broken in production - and the drift check could not see
it, because both sides of that check run inside the server.

The shape of the duplication

Two forms, and both appear in these files:

    proxy_pass http://...; proxy_set_header Host $host;
    proxy_set_header Host $host;              <- the repeat

and

    proxy_set_header Host $host;
    ...
    proxy_set_header Host $host;              <- the repeat

So the rule is: keep the FIRST Host header in a block and drop the
rest. The first one is the one on the proxy_pass line, and dropping
that instead would leave the block with no Host at all - which sends
the backend's own default and breaks virtual hosting.

Usage: fix_duplicate_host_header.py <vhost> [...]
"""
import re
import sys


def fix(text):
    out = []
    n = 0
    lines = text.splitlines(keepends=True)

    # find the blocks: from "location" to its closing brace
    i = 0
    while i < len(lines):
        if re.search(r"^\s*location\b", lines[i]):
            # the block's extent
            j = i
            depth = 0
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                if depth <= 0 and "{" in "".join(lines[i:j+1]):
                    break
                j += 1
            block = lines[i:j+1]

            # the Host headers in this block, in order
            hosts = []
            for k, ln in enumerate(block):
                if re.search(r"proxy_set_header\s+Host\s+\$host\s*;", ln):
                    hosts.append(k)

            if len(hosts) > 1:
                # keep the first, blank the rest
                for k in reversed(hosts[1:]):
                    # if the Host header is on the SAME line as a
                    # proxy_pass, removing the whole line would remove
                    # the proxy_pass too - so only the header part goes
                    block[k] = re.sub(
                        r"\s*proxy_set_header\s+Host\s+\$host\s*;", "", block[k])
                    n += 1
                # and the line it left behind, if it is now empty
                for k in range(len(block) - 1, -1, -1):
                    if block[k].strip() == "":
                        del block[k]
                    else:
                        break

            lines[i:j+1] = block
            i = j + 1
            continue
        i += 1

    return "".join(lines), n


def main():
    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8").read()
        out, n = fix(src)
        if n:
            open(path, "w", encoding="utf-8").write(out)
            print(f"  {path}: {n} duplicate Host header(s) removed")
        else:
            print(f"  {path}: already clean")


if __name__ == "__main__":
    main()
