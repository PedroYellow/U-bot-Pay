#!/usr/bin/env python3
"""Regression test for the nginx CORS map collision.

The bug
-------
Three vhosts (ubotpay, usonicpay, ustartkpay) each declared:

    map $http_origin $cors_origin { ... }

nginx `map` output variables are GLOBAL to the configuration. They are not
scoped to the file that declares them, and not scoped to the server block.
Declaring the same output variable in three included files does not create
three maps - it creates one, and the last one loaded wins.

So every vhost was silently using ustartkpay's allowlist:

    api.usonicpay.com + Origin: https://ustartkpay.com
      -> Access-Control-Allow-Origin: https://ustartkpay.com

    api.usonicpay.com + Origin: https://usonicpay.com
      -> (no header at all)

Two consequences, both serious:

1. usonicpay.com was completely broken. Every fetch to its own API was
   blocked by the browser as a CORS violation - no dashboard, no treasury
   address, no payments. The page rendered but could do nothing.

2. The domains could read each other's API responses. A script on
   ustartkpay.com could call api.usonicpay.com and read the reply.

The reason it took so long to find: the configs were byte-identical in the
relevant region, the vhost was provably the one being served (an
X-Who-Serves probe header confirmed it), and nginx -t passed throughout.
Nothing about the config looked wrong. Only the runtime behaviour differed.

What this test checks
---------------------
1. No two vhosts declare the same map output variable.
2. Each API host allows its own origins and refuses everyone else's,
   including a domain that is allowed elsewhere.
3. The ubotpay origin is still allowed on api.ubotpay.com - the fix must
   not have narrowed the main site.
"""
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

NGINX_VHOSTS = [
    ("ubotpay", "ubotpay.com"),
    ("usonicpay", "usonicpay.com"),
    ("ustartkpay", "ustartkpay.com"),
]

# Domains that must never be able to read each other's API.
FOREIGN = [
    "https://ubotpay.com",
    "https://usonicpay.com",
    "https://ustartkpay.com",
    "https://evil.example",
]

failures = []
notes = []


def check(cond, msg):
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        failures.append(msg)


def cors_origin(api_host, origin):
    """Return the Access-Control-Allow-Origin the API returns, or None."""
    req = urllib.request.Request(
        f"https://{api_host}/dashboard",
        headers={"Origin": origin, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.headers.get("Access-Control-Allow-Origin")
    except urllib.error.HTTPError as e:
        return e.headers.get("Access-Control-Allow-Origin")
    except Exception:
        return None


print("=" * 72)
print("C1: every vhost declares a unique map output variable")
print("=" * 72)

seen = {}
for name, domain in NGINX_VHOSTS:
    path = f"/etc/nginx/sites-available/{name}"
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except Exception as e:
        check(False, f"{name}: could not read {path} ({type(e).__name__})")
        continue

    vars_ = re.findall(r"map\s+\$http_origin\s+\$([A-Za-z_][A-Za-z0-9_]*)", src)
    check(len(vars_) == 1, f"{name}: declares exactly one cors map (found {vars_})")
    if not vars_:
        continue
    var = vars_[0]
    check(var not in seen,
          f"{name}: map variable ${var} is unique (not already used by "
          f"{seen.get(var, '-')})")
    seen.setdefault(var, name)

    # The variable it declares must be the one it consumes. There may be
    # more than one add_header (the HTTPS block and the port-80 redirect
    # block both carry it); what matters is that none of them consume a
    # different variable.
    consumes = re.findall(r"add_header\s+Access-Control-Allow-Origin\s+\$([A-Za-z_][A-Za-z0-9_]*)", src)
    check(bool(consumes) and all(c == var for c in consumes),
          f"{name}: every add_header consumes ${var} (found {consumes})")

    # The allowlist must cover the apex, www and agent hosts. nginx regexes
    # escape the dots, so compare against the escaped form.
    pat = re.search(r"map\s+\$http_origin\s+\$" + re.escape(var) + r"\s*\{([^}]*)\}", src)
    if pat:
        body = pat.group(1)
        # The nginx regex is written as an alternation inside one group:
        #   ~^https://(usonicpay|www\.usonicpay|agent\.usonicpay)\.com$
        # so the apex appears as a bare token and the trailing \.com
        # applies to the whole group. Matching "usonicpay\.com" as a
        # literal substring would never succeed.
        # The label is what appears inside the group; the ".com" suffix
        # lives outside it, after the closing paren. So compare on the
        # first label of the domain, not the whole domain.
        label = domain.split(".")[0]
        check(re.search(r"\(\s*" + re.escape(label) + r"\s*[|)]", body),
              f"{name}: allowlist includes the apex {domain}")
        for prefix in ("www", "agent"):
            # nginx escapes the dot for its own regex engine, so the text
            # on disk is "www\.usonicpay" - one backslash before the dot.
            token = prefix + "\\." + label
            check(token in body,
                  f"{name}: allowlist includes {prefix}.{domain}")
        # And nothing from another domain may leak in.
        for other_name, other_domain in NGINX_VHOSTS:
            if other_name == name:
                continue
            check(other_domain.replace(".", r"\.") not in body,
                  f"{name}: allowlist does not mention {other_domain}")

print()
print("=" * 72)
print("C2: each API serves its own origins and refuses foreign ones")
print("=" * 72)

for name, domain in NGINX_VHOSTS:
    api = f"api.{domain}"
    for origin in (f"https://{domain}", f"https://www.{domain}"):
        got = cors_origin(api, origin)
        check(got == origin,
              f"{api} allows {origin} (got {got!r})")

    for origin in FOREIGN:
        if origin == f"https://{domain}":
            continue
        got = cors_origin(api, origin)
        check(got is None,
              f"{api} refuses {origin} (got {got!r})")

print()
print("=" * 72)
print(f"{len(failures)} failure(s)")
print("=" * 72)
for f in failures:
    print(f"  - {f}")

sys.exit(1 if failures else 0)
