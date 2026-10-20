"""
Regression tests for CORS (audit 20 Oct round 11).

Both apps configured FastAPI's CORSMiddleware with allow_origins=["*"], and nginx
also sets Access-Control-Allow-Origin from an allowlist map. The middleware answers
first, so every response carried BOTH headers:

    access-control-allow-origin: *                      <- FastAPI
    Access-Control-Allow-Origin: https://ubotpay.com     <- nginx

The nginx allowlist was therefore decorative. Any site could read /health,
/pricing and /turn from the browser. Fixing it in nginx alone would have changed
nothing, because the wildcard is added upstream in the application.

    python3 test_cors.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []

API_PY = os.path.join(HERE, "api.py")
AGENT_PY = os.path.join(HERE, "dataagent.py")

# What nginx's $cors_origin map accepts. The application list has to match it or
# one of the two ends up refusing legitimate first-party requests.
NGINX_ALLOWED = {"https://ubotpay.com", "https://agent.ubotpay.com"}


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:54} got={got!r} want={want!r}")


def strip_comments(src):
    """Drop comments so the old pattern named in an explanatory comment is not
    mistaken for the pattern itself."""
    return re.sub(r"#.*", "", src)


def default_origins(src):
    m = re.search(r'UBOT_CORS_ORIGINS",\s*\n?\s*"([^"]+)"', src)
    return {o.strip().rstrip("/") for o in m.group(1).split(",")} if m else set()


def t_no_wildcard_in_either_app():
    for label, path in (("api.py", API_PY), ("dataagent.py", AGENT_PY)):
        code = strip_comments(open(path, encoding="utf-8").read())
        check(f"{label}: no allow_origins wildcard", 'allow_origins=["*"]' in code, False)
        check(f"{label}: no allow_methods wildcard", 'allow_methods=["*"]' in code, False)
        check(f"{label}: no allow_headers wildcard", 'allow_headers=["*"]' in code, False)


def t_credentials_are_off():
    """ACAO:* is illegal together with credentials. The API uses headers, not
    cookies, so this costs nothing and forbids the wildcard outright."""
    for label, path in (("api.py", API_PY), ("dataagent.py", AGENT_PY)):
        code = strip_comments(open(path, encoding="utf-8").read())
        check(f"{label}: allow_credentials=False", "allow_credentials=False" in code, True)
        check(f"{label}: credentials never enabled", "allow_credentials=True" in code, False)


def t_first_party_origins_allowed():
    for label, path in (("api.py", API_PY), ("dataagent.py", AGENT_PY)):
        got = default_origins(open(path, encoding="utf-8").read())
        for origin in sorted(NGINX_ALLOWED):
            check(f"{label}: allows {origin}", origin in got, True)
        # www is what users type, and nginx maps it away — but the browser sends
        # Origin verbatim, so it has to be listed here too.
        check(f"{label}: allows www.ubotpay.com", "https://www.ubotpay.com" in got, True)


def t_third_party_not_allowed():
    """The whole point: evil.com must not be listed, and nothing may reintroduce
    a wildcard or a prefix match that would let it through."""
    for label, path in (("api.py", API_PY), ("dataagent.py", AGENT_PY)):
        got = default_origins(open(path, encoding="utf-8").read())
        for bad in ("https://evil.com", "*", "null", "https://ubotpay.com.evil.com"):
            check(f"{label}: does not allow {bad}", bad in got, False)


def t_nginx_map_matches():
    """nginx still emits its own header, so the two lists must agree or one layer
    silently stops allowing first-party traffic."""
    got = default_origins(open(API_PY, encoding="utf-8").read())
    check("covers the two hosts nginx allows", NGINX_ALLOWED <= got, True)


def t_overridable_by_env():
    for label, path in (("api.py", API_PY), ("dataagent.py", AGENT_PY)):
        check(f"{label}: list is configurable",
              "UBOT_CORS_ORIGINS" in open(path, encoding="utf-8").read(), True)


def t_x402_header_allowed():
    """The DataAgent authenticates with X-Payment. Restricting CORS must not break
    the header the product's payment flow depends on."""
    code = strip_comments(open(AGENT_PY, encoding="utf-8").read())
    check("X-Payment still allowed", "X-Payment" in code, True)


if __name__ == "__main__":
    t_no_wildcard_in_either_app()
    t_credentials_are_off()
    t_first_party_origins_allowed()
    t_third_party_not_allowed()
    t_nginx_map_matches()
    t_overridable_by_env()
    t_x402_header_allowed()
    print("=" * 76)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
