#!/usr/bin/env python3
"""Call any Hostinger MCP tool over stdio with an explicit working key.

Use when the registered MCP returns 401 (its HOSTINGER_API_TOKEN expired) but another
key in /data/.env still answers 200 on /api/hosting/v1/websites.

  set -a; . /data/.env; set +a
  export HOSTINGER_API_TOKEN="$HOSTINGER_API_KEY"

  # deploy a site
  python3 mcp_call.py deployStaticWebsite friendsofpedro.school /abs/path/site.zip
  # create the website first (attaches domain to the plan)
  python3 mcp_call.py createWebsiteV1 friendsofpedro.school '{"order_id": 1005013776}'
"""
import json
import os
import subprocess
import sys

if len(sys.argv) < 3:
    sys.exit(__doc__)

tool = sys.argv[1]
domain = sys.argv[2]
if tool == "hosting_deployStaticWebsite":
    archive = os.path.abspath(sys.argv[3])
    if not os.path.isfile(archive):
        sys.exit(f"archive not found: {archive}")
    args = {"domain": domain, "archivePath": archive, "removeArchive": False}
else:
    extra = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    args = {"domain": domain, **extra}

if not os.environ.get("HOSTINGER_API_TOKEN"):
    sys.exit("HOSTINGER_API_TOKEN not set — export the key that returned 200 on the probe")

p = subprocess.Popen(
    ["hostinger-api-mcp"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, env=os.environ,
)


def send(msg):
    p.stdin.write(json.dumps(msg) + "\n")
    p.stdin.flush()


def recv(msg_id):
    while True:
        line = p.stdout.readline()
        if not line:
            sys.exit("mcp closed: " + p.stderr.read()[-800:])
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if m.get("id") == msg_id:
            return m


send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                 "clientInfo": {"name": "hermes", "version": "1"}}})
recv(1)
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
      "params": {"name": tool, "arguments": args}})
res = recv(2)
p.terminate()
print(json.dumps(res, indent=2)[:3000])
sys.exit(0 if "error" not in json.dumps(res).lower() or "success" in json.dumps(res).lower() else 1)
