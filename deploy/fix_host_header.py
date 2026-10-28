#!/usr/bin/env python3
"""Make the API locations forward the original Host header.

nginx's proxy_pass sets the upstream Host to the address it is proxying
to unless proxy_set_header Host says otherwise. Every API location in
the vhosts omitted it, so the backend saw "Host: 127.0.0.1:8090" for
every request, from every hostname.

That was harmless until the backend started answering per-host. The
public treasury endpoint now resolves the wallet and the block explorer
by the calling host, so a whitelabel that settles on a different chain
would have been told about the Avalanche treasury and a snowtrace.io
link - because it could not tell which site was asking.

The fix is to set proxy_set_header Host $host on the API locations, the
same way the /approve and /turn locations already do.

The vhost format puts each location on a single line, so this works
line by line rather than with a regex over the whole file. A regex that
tried to match "location ... { ... }" ran past the closing brace into
the next block and corrupted the config; lines do not have that
problem.

Usage: fix_host_header.py <vhost-path> [...]
"""
import re
import sys

# A location line that proxies to the API, with no Host header yet.
PROXY_RE = re.compile(r"(proxy_pass\s+http://127\.0\.0\.1:8090[^;]*;)")
HOST_RE = re.compile(r"proxy_set_header\s+Host\s")


def fix_line(line):
    """Return (new_line, changed). Only touch API proxying lines."""
    if "proxy_pass http://127.0.0.1:8090" not in line:
        return line, False
    if HOST_RE.search(line):
        return line, False
    # Put it right after the proxy_pass so the order reads naturally.
    new_line = PROXY_RE.sub(r"\1 proxy_set_header Host $host;", line, count=1)
    return new_line, new_line != line


def main():
    if len(sys.argv) < 2:
        print("usage: fix_host_header.py <vhost-path> [...]", file=sys.stderr)
        return 2
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()

        changed = 0
        for i, line in enumerate(lines):
            new_line, did = fix_line(line)
            if did:
                lines[i] = new_line
                changed += 1

        if changed:
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            print(f"{path}: set Host on {changed} location(s)")
        else:
            print(f"{path}: nothing to change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
