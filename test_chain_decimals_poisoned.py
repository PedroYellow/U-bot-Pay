"""
Round 24: chain._balance_usdc poisoned the global _DEC.

The previous code assigned a Contract object to the module-level _DEC and
read decimals() into a local `dec`. The function returned the right value,
but the global stayed a Contract. Any later chain.decimals() or
chain.to_units() crashed with `TypeError: unsupported operand type(s) for
** or pow(): 'int' and 'Contract'`.

This test pins the cascade with explicit Python against the live venv.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def chk(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print("%s  %-50s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want))


MOCK_BODY = r'''
class U:
    class _Fxns:
        def __init__(self, parent):
            pass
        def decimals(self):
            return _Bo
        def balanceOf(self, *a, **k):
            return 1000000
    def __init__(self):
        self.functions = self._Fxns(self)

class _Bo:
    def __init__(self):
        pass
    def call(self):
        return 6

class _W:
    def __init__(self):
        self._u = U()
    def contract(self, addr, abi):
        return self._u

w3 = _W()
# w3.eth is missing on this stub; chain.py reads w3l.eth.contract(...).
# Set it up so w3l.eth.contract returns the same U instance.
w3.eth = type('Eth', (), {'contract': w3.contract})()
'''


def test_dec_unchanged_after_balance_usdc():
    """_DEC stays an int after _balance_usdc, not a Contract."""
    code = MOCK_BODY + '''
class _C:
    id = 8453
    usdc = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
c = _C()
import chain
chain._CHAIN_RUNTIME[8453] = (c, w3, U())
chain._balance_usdc(w3, c, "0x713261F2151a759d9D935e6De2DB1d63A98176f6")
print(type(chain._DEC).__name__)
'''
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", code],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s
%s" % (r.returncode, r.stdout, r.stderr))
    out = r.stdout.strip().split("\n")[-1]
    chk("_DEC is int after _balance_usdc", out, "int")


def test_to_units_after_balance_usdc():
    """to_units(1.0) still returns 1_000_000, not a TypeError."""
    code = MOCK_BODY + '''
class _C:
    id = 8453
    usdc = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
c = _C()
import chain
chain._CHAIN_RUNTIME[8453] = (c, w3, U())
chain._balance_usdc(w3, c, "0x713261F2151a759d9D935e6De2DB1d63A98176f6")
print(chain.to_units(1.0))
'''
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", code],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s
%s" % (r.returncode, r.stdout, r.stderr))
    out = r.stdout.strip().split("\n")[-1]
    chk("to_units(1.0) still = 1000000", out, "1000000")


def test_balance_usdc_source_uses_local_var():
    """The function must NOT touch chain._DEC at all any more."""
    src = open(os.path.join(HERE, "chain.py"), encoding="utf-8").read()
    i = src.index("def _balance_usdc(")
    j = src.index("\ndef ", i + 10)
    body = src[i:j]
    chk("_balance_usdc no longer assigns to global _DEC",
        "global _DEC" not in body, True)


def test_double_balance_usdc_still_safe():
    """Run two consecutive _balance_usdc calls (different chains) and
    confirm the global does not retain a Contract afterwards."""
    code = MOCK_BODY + '''
class _C1:
    id = 8453
    usdc = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
class _C2:
    id = 43114
    usdc = "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"
c1, c2 = _C1(), _C2()
import chain
chain._CHAIN_RUNTIME[8453] = (c1, w3, U())
chain._CHAIN_RUNTIME[43114] = (c2, w3, U())
chain._balance_usdc(w3, c1, "0x713261F2151a759d9D935e6De2DB1d63A98176f6")
chain._balance_usdc(w3, c2, "0x713261F2151a759d9D935e6De2DB1d63A98176f6")
print(type(chain._DEC).__name__, chain.to_units(1.0))
'''
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", code],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s
%s" % (r.returncode, r.stdout, r.stderr))
    out = r.stdout.strip().split("\n")[-1]
    chk("after 2x _balance_usdc: int, to_units=1e6", out, "int 1000000")


if __name__ == "__main__":
    test_dec_unchanged_after_balance_usdc()
    test_to_units_after_balance_usdc()
    test_balance_usdc_source_uses_local_var()
    test_double_balance_usdc_still_safe()
    print("=" * 76)
    print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
