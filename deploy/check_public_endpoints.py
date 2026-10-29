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

# The destination address has to match the DESTINATION chain's format.
#
# The source must stay EVM in every case - the user signs an
# eth_sendTransaction payload on it, and from_address is validated as
# 0x + 40 hex. But the destination may be a non-EVM chain, where
# to_address is a base58 mint instead of a 0x address.
#
# So the Sonic deployment gets a base58 destination and the others get
# an EVM one. With an EVM address on the Sonic deployment the service
# answers 400 - which is the service validating the request, not a
# broken endpoint.
TO_ADDRESS = {
    "api.ubotpay.com": "0x29219dd400f2Bf60E5a23d13Be72B486D4038894",
    "api.usonicpay.com": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
    "api.ustartkpay.com": "0x29219dd400f2Bf60E5a23d13Be72B486D4038894",
}

# LI.FI has no quote for every pair at every size, and a 502 from the
# upstream is transient. A 502 is not a broken endpoint, so it is
# reported separately from the failures.
TRANSIENT = (502, 503, 504)


def get(url, timeout=40):
    """Return (status, body). The body is NOT truncated.

    It was truncated to 400 bytes at first, and the quote's JSON is
    longer than that - so the check read a partial document, failed to
    parse it, and reported a working quote as a failure.
    """
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "ubotpay-endpoint-check/1.0",
            "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, str(e)[:200].encode()


def main():
    verbose = "--verbose" in sys.argv
    failures = []
    checked = 0

    for domain in DOMAINS:
        print(f"  {domain}")
        for path, want in ENDPOINTS:
            status, body = get(f"https://{domain}{path}")
            checked += 1
            ok = status in ((want,) if isinstance(want, int) else want)
            if not ok:
                failures.append(f"{domain}{path} -> {status} "
                                f"(want {want})")
            print(f"    {'OK  ' if ok else 'FAIL'} {path:22s} {status}")

        # the quote, with its parameters and an address that matches
        # the destination chain's format
        url = f"https://{domain}{QUOTE}".format(
            to_address=TO_ADDRESS.get(domain,
                                     TO_ADDRESS["api.ubotpay.com"]))
        status, body = get(url)
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
