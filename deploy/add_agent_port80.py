#!/usr/bin/env python3
"""Add agent.<domain> to a vhost's port-80 server_name.

certbot's webroot authenticator fetches
http://agent.<domain>/.well-known/acme-challenge/<token> over plain HTTP.
The vhost's port-80 block only listed the apex, www and api, so the
request fell through to the default server and 404'd - which is what
certbot reported as "Invalid response ... 404".

Usage: add_agent_port80.py <vhost-path> <domain>
"""
import re
import sys


def main():
    if len(sys.argv) != 3:
        print("usage: add_agent_port80.py <vhost-path> <domain>", file=sys.stderr)
        return 2
    vhost, domain = sys.argv[1], sys.argv[2]
    want = f"agent.{domain}"

    s = open(vhost, encoding="utf-8").read()

    m = re.search(r"(listen 80;\n\s*server_name )([^;]+);", s)
    if not m:
        print("could not find the port-80 server block", file=sys.stderr)
        return 1

    names = m.group(2).split()
    if want in names:
        print(f"{want} already in the port-80 server_name")
        return 0

    names.append(want)
    s = s[:m.start(2)] + " ".join(names) + s[m.end(2):]
    open(vhost, "w", encoding="utf-8").write(s)
    print("port-80 server_name is now:", " ".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
