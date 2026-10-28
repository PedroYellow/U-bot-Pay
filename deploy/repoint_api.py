#!/usr/bin/env python3
"""Repoint a whitelabel vhost's API locations at its own service.

Every API location in the usonicpay vhost proxied to 127.0.0.1:8090 -
the main U-bot Pay service, which is the Avalanche deployment. So
api.usonicpay.com was answering from Avalanche: its /treasury reported
the Avalanche wallet and a snowtrace.io link, and its /dashboard
reported chain "avalanche-mainnet".

The Sonic deployment runs on 8094 (USonicPay). The locations have to
point there.

The DataAgent backend on 8091 is deliberately left alone - it is a
separate service that the whitelabels share.

Usage: repoint_api.py <vhost-path> <old-port> <new-port>
"""
import re
import sys

path = sys.argv[1]
old = sys.argv[2]
new = sys.argv[3]

src = open(path, encoding="utf-8").read()
pattern = re.compile(r"(127\.0\.0\.1:)" + re.escape(old) + r"\b")

out, n = pattern.subn(r"\g<1>" + new, src)
if n:
    open(path, "w", encoding="utf-8").write(out)
print(f"{path}: {n} location(s) repointed from {old} to {new}")
