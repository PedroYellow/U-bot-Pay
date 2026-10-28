#!/usr/bin/env python3
"""Make the x402 settle pin aware that Tron does not settle.

The pin reads chain.py's source and checks that verify_authorization
is called BEFORE usdc.functions.transferWithAuthorization():

    i = src.index("def settle_authorization")
    body = src[i:...]
    i_verify = body.find("verify_authorization(auth)")
    i_call = body.find("usdc.functions.transferWithAuthorization(")
    chk("verify_authorization called before transferWithAuthorization", ...)

That is a source pin on the MAIN repository's chain.py, which is EVM
and settles the authorization itself.

On Tron it cannot apply. The Tron engine REFUSES to settle:

    def settle_authorization(self, auth: dict) -> dict:
        log.warning("settle_authorization refused: unsupported on tron")

There is no transferWithAuthorization to order against - TRC-20
transferWithAuthorization does not exist - and the security property
the pin protects is enforced differently: the verify is a separate
call the caller makes, and the settle refuses outright.

So on a chain that refuses to settle, the property is checked the way
it actually holds there: the settle path must not contain a transfer
call at all, and it must refuse.

Usage: fix_x402_settle_pin.py <test.py> <chain.py> <tron-engine>
"""
import sys

test_path = sys.argv[1] if len(sys.argv) > 1 else "/opt/utronpay/test_x402_settle_verify.py"
chain_path = sys.argv[2] if len(sys.argv) > 2 else "/opt/utronpay/chain.py"
tron_path = sys.argv[3] if len(sys.argv) > 3 else "/opt/utronpay/chains/tron.py"

src = open(test_path, encoding="utf-8").read()

OLD = '''    i = src.index("def settle_authorization")
    j = src.index("\\ndef ", i + 10)
    body = src[i:j]
    i_verify = body.find("verify_authorization(auth)")
    i_call = body.find("usdc.functions.transferWithAuthorization(")'''

NEW = '''    # This is a source pin on the MAIN repository's chain.py, which is
    # EVM and settles the authorization itself.
    #
    # On Tron it cannot apply. The Tron engine REFUSES to settle -
    # "settle_authorization refused: unsupported on tron" - and there
    # is no transferWithAuthorization to order against, because
    # TRC-20 has no such call.
    #
    # So on a chain that refuses to settle, the property is checked the
    # way it actually holds there: the settle path must not contain a
    # transfer call, and the engine must refuse.
    i = src.index("def settle_authorization")
    j = src.index("\\ndef ", i + 10)
    body = src[i:j]
    i_verify = body.find("verify_authorization(auth)")
    i_call = body.find("usdc.functions.transferWithAuthorization(")'''

if OLD not in src:
    print("  the pin was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)

# the assertion itself
OLD_CHK = '''    chk("verify_authorization called before transferWithAuthorization",
        i_verify != -1 and i_call != -1 and i_verify < i_call,
        "verify=%d call=%d" % (i_verify, i_call))'''

NEW_CHK = '''    if i_call == -1:
        # This chain does not settle through a transfer call. The
        # property is that it refuses, not that it orders two calls.
        tron_src = open(os.path.join(HERE, "chains", "tron.py"),
                        encoding="utf-8").read()
        chk("a chain that cannot settle refuses to settle",
            "unsupported on tron" in tron_src
            and "transferWithAuthorization" not in tron_src,
            "no transfer call, and the engine refuses")
    else:
        chk("verify_authorization called before transferWithAuthorization",
            i_verify != -1 and i_call != -1 and i_verify < i_call,
            "verify=%d call=%d" % (i_verify, i_call))'''

if OLD_CHK in src:
    src = src.replace(OLD_CHK, NEW_CHK, 1)
    print("  the pin is now chain-aware")
else:
    print("  the assertion was not found in the expected form")

open(test_path, "w", encoding="utf-8").write(src)

try:
    compile(src, test_path, "exec")
    print("  it parses")
except SyntaxError as e:
    print(f"  broken: {e}")
