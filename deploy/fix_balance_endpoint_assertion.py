#!/usr/bin/env python3
"""Fix the balance-endpoint assertion to match the intended design.

The assertion was:

    _r = _c.get("/balance/AgbmMLTnmEKN4FoTfim4y9bdjxqWGLs9mwf3MTSe3CbJ")
    chk("the public balance endpoint answers 200 for a Solana address",
        _r.status_code == 200, _r.status_code)

Two things are wrong with it.

**The endpoint needs auth.** /balance takes a Bearer JWT, or the
internal x-api-key header. A balance is an inventory of a spendable
asset, and the audit made it authenticated. So it answers 401 without
credentials - which is correct, and the test was asserting the
behaviour the audit removed.

**The path parameter is not a thing.** The route is /balance, not
/balance/<address>. So the request was for a route that does not
exist, and FastAPI's 404 would have been the answer if the auth had
not come first.

The test now calls the real route with the deployment's API key, and
additionally checks that the endpoint refuses an unauthenticated
caller - which is the property worth keeping.

Usage: fix_balance_endpoint_assertion.py <test.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usolpay/test_usolpay_e2e.py"

src = open(path, encoding="utf-8").read()

OLD = '''    _c = TestClient(_api.app)
    _r = _c.get("/balance/AgbmMLTnmEKN4FoTfim4y9bdjxqWGLs9mwf3MTSe3CbJ")
    chk("the public balance endpoint answers 200 for a Solana address",
        _r.status_code == 200, _r.status_code)'''

NEW = '''    _c = TestClient(_api.app)

    # The balance endpoint is AUTHENTICATED. A balance is an inventory
    # of a spendable asset, and the audit made it so: it takes a
    # Bearer JWT, or the internal x-api-key header. The test called it
    # publicly and expected 200 - the behaviour the audit removed - so
    # it answered 401.
    #
    # It also used a path parameter that the route does not have. The
    # route is /balance, not /balance/<address>.
    _key = os.environ.get("USOL_API_KEY", "")
    _r = _c.get("/balance", headers={"x-api-key": _key})
    chk("the balance endpoint answers 200 for the demo user",
        _r.status_code == 200, _r.status_code)

    _r_unauth = _c.get("/balance")
    chk("the balance endpoint refuses an unauthenticated caller",
        _r_unauth.status_code == 401, _r_unauth.status_code)'''

if OLD not in src:
    print("  the assertion was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
open(path, "w", encoding="utf-8").write(src)
print(f"{path}: the assertion now uses the real route, with auth")
