#!/usr/bin/env python3
"""Check the nginx vhosts for a duplicated `proxy_set_header Host`.

The bug

    location = /bridge/quote {
        proxy_pass http://127.0.0.1:8090/bridge/quote; proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Host $host;        <- <- the second one
        proxy_hide_header Access-Control-Allow-Origin;
    }

Two `proxy_set_header Host` lines in one block means nginx sends TWO
Host headers to the backend. uvicorn rejects a request with two Host
headers:

    HTTP/1.1 400 Bad Request
    Invalid HTTP request received.

So /bridge/quote returned 400 to EVERY caller from the internet,
while working perfectly from inside the VPS - and the drift check
could not see it, because both sides of that check run inside the
server.

Why a static check

The runtime symptom is a 400 from uvicorn that looks like a
malformed request, and it only appears from outside. A test that
has to make a request from outside to notice is a test that needs
the internet, and it would be flaky.

The static check is exact: it parses each location block and counts
the Host headers in it. One is correct. Zero means the backend gets
no Host at all, which breaks virtual hosting. Two is the bug.

Usage: check_nginx_host_headers.py <vhost-dir-or-file> [...]
"""
import os
import re
import sys

RESULTS = []


def chk(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print("%s  %-58s %s" % ("PASS" if cond else "FAIL", name, detail))


def blocks(lines):
    """Yield (start_line, block_lines) for each location block."""
    i = 0
    while i < len(lines):
        if re.search(r"^\s*location\b", lines[i]):
            j, depth = i, 0
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                if depth <= 0 and "{" in "".join(lines[i:j + 1]):
                    break
                j += 1
            yield i + 1, lines[i:j + 1]
            i = j + 1
            continue
        i += 1


def check(path):
    lines = open(path, encoding="utf-8").read().splitlines()
    name = os.path.basename(path)
    problems = []

    for start, block in blocks(lines):
        hosts = [k for k, ln in enumerate(block)
                 if re.search(r"proxy_set_header\s+Host\s+\$host\s*;", ln)]
        if len(hosts) > 1:
            # THE bug. Two Host headers means nginx sends two, and
            # uvicorn rejects the request with "Invalid HTTP request
            # received".
            problems.append(
                f"the block at line {start} has {len(hosts)} Host headers")
        # A block with a proxy_pass and NO Host header is FINE. nginx
        # passes the original Host through by default, and several
        # locations in usolpay and utronpay rely on that. Only the
        # duplicate is the bug - requiring a Host header would flag
        # working configurations.

    if problems:
        for p in problems:
            print("    %s: %s" % (name, p))
    chk(f"{name}: one Host header per location", not problems,
        f"{len(problems)} problem(s)" if problems else "clean")


def main():
    targets = []
    for arg in sys.argv[1:]:
        if os.path.isdir(arg):
            for f in sorted(os.listdir(arg)):
                # the live vhosts have NO extension - they are
                # /etc/nginx/sites-available/ubotpay, not .conf - and
                # the repository's are .conf. So take every file that
                # looks like a vhost, not just the .conf ones.
                if f.endswith(".conf") or (
                        not f.startswith(".") and "bak" not in f
                        and "." not in f):
                    targets.append(os.path.join(arg, f))
        elif os.path.exists(arg):
            targets.append(arg)

    for path in targets:
        check(path)

    print()
    passed = sum(1 for r in RESULTS if r)
    print(f"{passed}/{len(RESULTS)} passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
