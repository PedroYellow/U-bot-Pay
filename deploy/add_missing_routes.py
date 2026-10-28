#!/usr/bin/env python3
"""Add the missing API routes to a vhost.

`/voice/session` exists in the API and the front-end calls it, but no
vhost had a location for it. It fell through to the SPA catch-all, and
`try_files` rewrote it to index.html - which answers 405 to a POST. So
the voice feature was dead in production on every deployment, including
the main one, and nothing in the logs said why: the request arrived,
was rewritten, and returned 405.

This adds the location, pointing at the deployment's own service.

The API paths are checked against the running service, so a route that
does not exist is reported rather than added as a 502.

Usage: add_missing_routes.py <vhost-path> <service-port>
"""
import json
import re
import subprocess
import sys
import urllib.request

vhost = sys.argv[1]
port = sys.argv[2]

# API paths that the front-end calls, that need their own location so
# they are not rewritten to index.html by the SPA catch-all.
ROUTES = [
    ("= /voice/session", "/voice/session"),
    ("= /voice", "/voice"),
]

# what does the service actually expose?
try:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/openapi.json")
    with urllib.request.urlopen(req, timeout=15) as r:
        spec = json.load(r)
    paths = set(spec.get("paths", {}))
except Exception as e:
    print(f"could not read the OpenAPI spec from {port}: {type(e).__name__}")
    sys.exit(1)

print(f"the service exposes {len(paths)} paths")

src = open(vhost, encoding="utf-8").read()
added = []
skipped = []

for location, path in ROUTES:
    if path not in paths:
        skipped.append(f"{path} (not in the service's OpenAPI)")
        continue
    if f"location {location} " in src or f"location {location}{{" in src:
        skipped.append(f"{path} (already routed)")
        continue

    block = (
        f"    location {location} {{ proxy_pass http://127.0.0.1:{port}{path}; "
        f"proxy_set_header Host $host; proxy_hide_header Access-Control-Allow-Origin; "
        f"proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; "
        f"proxy_set_header X-Real-IP $remote_addr; }}\n"
    )

    # put it with the other API locations, before the SPA catch-all
    marker = "\n    location / {\n"
    i = src.index(marker)
    src = src[:i] + "\n" + block.rstrip("\n") + src[i:]
    added.append(path)

if added:
    open(vhost, "w", encoding="utf-8").write(src)
    print(f"added: {', '.join(added)}")
else:
    print("nothing added")
for s in skipped:
    print(f"  skipped: {s}")

# validate
r = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
print(r.stderr.strip().split("\n")[-1] if r.returncode != 0 else "nginx config is valid")
sys.exit(1 if r.returncode != 0 else 0)
