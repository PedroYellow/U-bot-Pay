#!/usr/bin/env python3
"""Read the MCP response on stdin and check the deploy status.

The MCP wraps the payload:

    {"result":{"content":[{"type":"text","text":"<json>"}]}}

so the inner text is parsed as JSON and the deploy status is read
from it.

Used by tcb_sync_blocklist.sh to decide whether the deploy worked.
"""
import json
import sys

raw = sys.stdin.read().strip()

if not raw:
    sys.exit("deploy failed: no response")

try:
    outer = json.loads(raw)
    inner = json.loads(outer["result"]["content"][0]["text"])
except Exception as e:
    sys.exit(f"deploy failed: unparseable response ({e})")

if inner.get("deploy", {}).get("status") != "success":
    sys.exit(f"deploy failed: {inner}")

print("deploy ok")
