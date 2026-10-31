#!/usr/bin/env python3
"""Check every public endpoint, from OUTSIDE the server.

Two real bugs were found by doing exactly this, and neither was
visible from inside:

  - /bridge/quote answered 400 to every caller from the internet,
    while working perfectly from the VPS. The cause was a
    duplicated `proxy_set_header Host` in the nginx vhost: nginx sent
    two Host headers and uvicorn rejected the request.

  - /bridge/chains answered 500 on the Starknet deployment, because
    StarknetEngine inherits from nothing and so does not get
    ChainEngine's `explorer()`.

Both read as healthy from inside the server. The drift check, the
test suite and a manual curl all ran there - which is why they all
passed while the features were broken for real users.

So this runs from outside, over the public internet, against the
public domains. That is the only vantage point that shows what a user
sees.

Usage: check_public_endpoints.py [--verbose]
"""
import json
import sys
import urllib.error
import urllib.request

DOMAINS = [
    "api.ubotpay.com",
    "api.usonicpay.com",
    "api.ustartkpay.com",
    # These two were NOT in the list at first, and they were the two
    # with the broken /treasury route. The check only covers the
    # domains it was written for, so it could not see a bug in the
    # two it did not look at.
    "api.usolpay.com",
    "api.utronpay.com",
]

# The paths that MUST NOT exist. The API host used to answer 200 with
# the site's HTML for anything it had no route for - including these.
# A 200 there tells a scanner the path exists, and gives an API
# client HTML where it expected JSON.
FORBIDDEN = [
    "/.env",
    "/.git/config",
    "/server.key",
    "/seedphrase.txt",
]

# the endpoint, and what a healthy answer looks like
ENDPOINTS = [
    ("/health", 200),
    ("/pricing", 200),
    ("/treasury", 200),
    ("/dashboard", 200),
    ("/bridge/chains", 200),
    ("/push/vapid-key", 200),
    # the quote needs its parameters, so it is checked separately
    ("/docs", (200, 404)),
    ("/openapi.json", (200, 404)),
]

QUOTE = ("/bridge/quote?partner=best&from_chain=43114&to_chain=146"
         "&amount_usdc=100"
         "&from_address=0x713261F2151a759d9D935e6De2DB1d63A98176f6"
         "&to_address={to_address}")

# The destination address has to match the format the DEPLOYMENT
# expects - not the format of the chain the query names.
#
# The source must stay EVM in every case - the user signs an
# eth_sendTransaction payload on it, and from_address is validated as
# 0x + 40 hex. But the destination is validated by the deployment's
# own engine, and those differ:
#
#   - the Solana deployment's engine is an EVM engine, so it demands a
#     0x address: "to_address must be a 0x-prefixed EVM address"
#   - the Sonic deployment's engine is the Solana engine, so it wants
#     a base58 address
#
# With an EVM address on the Sonic deployment the service answers 400,
# which is the service validating the request, not a broken endpoint.
# And with a base58 address on the Solana deployment it answers 400
# the other way round.
TO_ADDRESS = {
    "api.ubotpay.com": "0x29219dd400f2Bf60E5a23d13Be72B486D4038894",
    "api.usonicpay.com": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
    "api.ustartkpay.com": "0x29219dd400f2Bf60E5a23d13Be72B486D4038894",
    "api.usolpay.com": "0x29219dd400f2Bf60E5a23d13Be72B486D4038894",
    "api.utronpay.com": "0x29219dd400f2Bf60E5a23d13Be72B486D4038894",
}

# LI.FI has no quote for every pair at every size, and a 502 from the
# upstream is transient. A 502 is not a broken endpoint, so it is
# reported separately from the failures.
TRANSIENT = (502, 503, 504)


def get(url, timeout=40):
    """Return (status, body, content_type). The body is NOT truncated.

    It was truncated to 400 bytes at first, and the quote's JSON is
    longer than that - so the check read a partial document, failed to
    parse it, and reported a working quote as a failure.

    The content type comes back too, and it matters: the API hosts
    fall through to the SPA fallback for a path they do not have a
    route for, and the fallback answers 200 with the site's HTML. So
    a JSON API can answer 200 with text/html, and that is a broken
    route that looks perfectly healthy if you only check the status.
    """
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "ubotpay-endpoint-check/1.0",
            "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")
    except Exception as e:
        return None, str(e)[:200].encode(), ""


def main():
    verbose = "--verbose" in sys.argv
    failures = []
    checked = 0

    for domain in DOMAINS:
        print(f"  {domain}")
        for path, want in ENDPOINTS:
            status, body, ctype = get(f"https://{domain}{path}")
            checked += 1
            ok = status in ((want,) if isinstance(want, int) else want)
            detail = ""
            # The API hosts fall through to the SPA fallback for a path
            # they have no route for, and the fallback answers 200
            # with the site's HTML. So a JSON API answering 200 with
            # text/html is a BROKEN route - and a status-only check
            # reads it as healthy.
            #
            # That is exactly what /treasury did on the Solana and
            # Tron deployments: no route, so the SPA answered 200
            # with the page.
            if ok and status == 200 and path not in ("/docs", "/openapi.json"):
                if "json" not in (ctype or "").lower():
                    ok = False
                    detail = f"it answered {ctype}, not JSON"
            if not ok:
                failures.append(f"{domain}{path} -> {status} "
                                f"(want {want})"
                                + (f" {detail}" if detail else ""))
            print(f"    {'OK  ' if ok else 'FAIL'} {path:22s} {status}"
                  + (f"  {detail}" if detail else ""))

        # the paths that must not be there
        for path in FORBIDDEN:
            status, body, ctype = get(f"https://{domain}{path}")
            checked += 1
            ok = status == 404
            if not ok:
                failures.append(f"{domain}{path} -> {status} (want 404)")
            print(f"    {'OK  ' if ok else 'FAIL'} {path:22s} {status}"
                  + ("" if ok else "  it should not exist"))

        # the quote, with its parameters and an address that matches
        # the destination chain's format
        url = f"https://{domain}{QUOTE}".format(
            to_address=TO_ADDRESS.get(domain,
                                     TO_ADDRESS["api.ubotpay.com"]))
        status, body, ctype = get(url)
        checked += 1
        ok = status == 200
        transient = False
        if ok:
            try:
                d = json.loads(body)
                # a 200 that is not a quote is not a working quote
                ok = bool(d.get("ok")) and bool(d.get("tool"))
                if verbose and ok:
                    print(f"        tool={d.get('tool')} "
                          f"out={d.get('to_amount_human')} "
                          f"fee={d.get('total_fee_usd')}")
            except Exception:
                ok = False
        elif status in TRANSIENT:
            # the upstream had no quote at that moment. Not a broken
            # endpoint, but worth seeing.
            transient = True
        if not ok and not transient:
            failures.append(f"{domain}/bridge/quote -> {status}")
        mark = "OK  " if ok else ("WARN" if transient else "FAIL")
        print(f"    {mark} {'/bridge/quote':22s} {status}"
              + ("  (the upstream had no quote)" if transient else ""))

    print()
    print(f"  {checked} endpoint(s) checked, {len(failures)} failure(s)")
    for f in failures:
        print(f"    - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
