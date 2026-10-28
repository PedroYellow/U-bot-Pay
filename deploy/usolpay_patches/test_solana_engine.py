"""
Tests for chains/solana.py against the real profile and a real signer,
without ever sending anything.

The engine is exercised in two states that matter:

  READ-ONLY  signer=None — what a replica, a health check, or a process
              that simply lacks the key looks like. Reads must work and
              every send must refuse.

  SIGNED     a signer backed by a freshly generated, throwaway keypair.
              Never funded, never funded by this test. Proves the send
              path validates and refuses in the right order, so the
              ordering is verified before a real treasury exists.

Nothing here broadcasts. The refusals are the assertion.
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.environ.get(
        "ENGINE_ROOT", os.path.dirname(os.path.abspath(__file__))))

from chains.registry import _load_yaml
from chains.solana import SolanaEngine, SPL_TOKEN_PROGRAM, _SHORT_ADDR_CHARS

PROFILE = os.environ.get(
        "PROFILE_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "profiles", "usolpay.yaml"))

RESULTS = []


def chk(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print("%s  %-56s %s" % ("PASS" if cond else "FAIL", name, detail))


def raises(name, fn, exc=Exception, needle=""):
    """Assert that `fn` refuses, and that the message is useful."""
    try:
        fn()
    except exc as e:
        msg = str(e)
        if needle and needle.lower() not in msg.lower():
            chk(name, False, "refused but message unhelpful: %s" % msg[:90])
        else:
            chk(name, True, "refused: %s" % msg[:70])
        return
    except Exception as e:
        chk(name, False, "wrong exception type: %s: %s" % (type(e).__name__, str(e)[:60]))
        return
    chk(name, False, "did NOT refuse")


prof = _load_yaml(PROFILE)

# ── construction from the profile ────────────────────────────────
eng = SolanaEngine(prof)

chk("name comes from the profile", eng.name == "USolPay", eng.name)
chk("stable symbol is USDC", eng.stable_symbol == "USDC", eng.stable_symbol)
chk("native symbol is SOL", eng.native_symbol == "SOL", eng.native_symbol)
chk("mint loaded from profile",
    eng.stable_address == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    eng.stable_address)
chk("decimals is 6", eng.decimals() == 6, eng.decimals())
chk("sdk tag is solana (for the conflict check)", eng.sdk == "solana", eng.sdk)
chk("evm is False", eng.evm is False, eng.evm)
chk("supports_x402 is False", eng.supports_x402 is False, eng.supports_x402)
chk("explorer base loaded", eng.explorer().startswith("https://solscan.io"),
    eng.explorer())
chk("chain id kept for bridge compat", eng.chain_id() == 1151111081099710,
    eng.chain_id())
chk("ata policy is create", eng._ata_policy == "create", eng._ata_policy)
chk("min sol reserve is 0.01", eng._min_sol_reserve == 0.01, eng._min_sol_reserve)
chk("SPL token program is the real one",
    SPL_TOKEN_PROGRAM == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")

# ── units ───────────────────────────────────────────────────────
chk("to_units(1) == 1_000_000", eng.to_units(1) == 1_000_000, eng.to_units(1))
chk("to_units('1.5') == 1_500_000", eng.to_units("1.5") == 1_500_000,
    eng.to_units("1.5"))
chk("to_units(0.000001) == 1", eng.to_units(0.000001) == 1, eng.to_units(0.000001))
chk("from_units(1_000_000) == 1", eng.from_units(1_000_000) == 1,
    eng.from_units(1_000_000))
# from_units returns Decimal on purpose (so no float ever touches money),
# so the round-trip must compare against a Decimal, not a float literal.
chk("round-trip 12.34", eng.from_units(eng.to_units(12.34)) == Decimal("12.34"),
    eng.from_units(eng.to_units(12.34)))
chk("from_units returns Decimal, never float",
    isinstance(eng.from_units(1_500_000), Decimal), type(eng.from_units(1)).__name__)
chk("from_units(1) == 1 exactly", eng.from_units(1_000_000) == Decimal(1),
    eng.from_units(1_000_000))

raises("to_units rejects >6 decimals", lambda: eng.to_units(1.0000001))
raises("to_units rejects garbage", lambda: eng.to_units("not-a-number"))
raises("to_units rejects None", lambda: eng.to_units(None))
# No float may sneak in through a computation.
chk("to_units accepts int 7", eng.to_units(7) == 7_000_000, eng.to_units(7))

# ── validation, read-only state ─────────────────────────────────
GOOD = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"
chk("valid address accepted", eng.is_address(GOOD))
chk("EVM address rejected", not eng.is_address("0x" + "a" * 40))
chk("Tron address rejected", not eng.is_address("TKVC8AzsercmUg69cmdojaVTnAixmWpCWb"))
chk("address with space rejected", not eng.is_address(GOOD + " "))
chk("address over 44 chars rejected", not eng.is_address("1" * 45))
chk("address under 43 chars rejected", not eng.is_address("1" * 42))
chk("43-char minimum is the documented constant", _SHORT_ADDR_CHARS == 43)
chk("EVM tx hash is not a Solana signature", not eng.is_tx_hash("0x" + "a" * 64))
chk("an address is not a signature", not eng.is_tx_hash(GOOD))
chk("empty tx rejected", not eng.is_tx_hash(""))
chk("None tx rejected", not eng.is_tx_hash(None))

# ── ATA derivation is deterministic and chain-correct ───────────
ata1 = eng.ata_address(GOOD)
ata2 = eng.ata_address(GOOD)
chk("ATA derivation is deterministic", ata1 == ata2, ata1[:20] + "...")
chk("ATA is itself a valid address", eng.is_address(ata1))
chk("ATA differs per owner", ata1 != eng.ata_address(
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"))
raises("ATA derivation rejects a bad owner", lambda: eng.ata_address("0xdeadbeef"))

# ── explorer urls ───────────────────────────────────────────────
chk("tx explorer url", eng.explorer_url(GOOD).endswith("/tx/" + GOOD),
    eng.explorer_url(GOOD)[:40])
chk("no tx -> no url", eng.explorer_url(None) is None)
chk("empty tx -> no url", eng.explorer_url("") is None)

# ── READ-ONLY state: reads work, sends refuse ────────────────────
chk("no signer configured", eng._signer is None)
raises("send without a signer refuses", lambda: eng.submit(GOOD, 1.0))
raises("send without a signer refuses even with a fee",
        lambda: eng.submit(GOOD, 1.0, fee=0.5))

# ── validation order: bad destination beats missing signer ──────
raises("invalid destination refuses first", lambda: eng.submit("not-an-address", 1.0))
raises("zero amount refuses", lambda: eng.submit(GOOD, 0))
raises("negative amount refuses", lambda: eng.submit(GOOD, -5))

# ── SIGNED state with a throwaway, unfunded keypair ─────────────
from solders.keypair import Keypair
from solders.pubkey import Pubkey as _Pk


class _ThrowawaySigner:
    """A signer over a keypair generated in memory for this test. It is
    never funded and this test never broadcasts; it exists so the send
    path's validation ORDER can be verified before a real treasury is
    created."""

    def __init__(self):
        self.kp = Keypair()
        self.signed = 0

    def pubkey(self):
        return self.kp.pubkey()

    def sign_transaction(self, t):
        self.signed += 1
        return t


signer = _ThrowawaySigner()
eng2 = SolanaEngine(prof, signer=signer)

chk("signed engine reports its treasury", eng2._treasury_address() == str(signer.pubkey()),
    str(eng2._treasury_address())[:16] + "...")
chk("treasury address is valid", eng2.is_address(eng2._treasury_address()))
chk("fresh keypair produced no signature yet", signer.signed == 0, signer.signed)

# An unfunded treasury must refuse on SOL, not on the signer.
raises("unfunded treasury refuses to send", lambda: eng2.submit(GOOD, 1.0),
        RuntimeError, "sol")

# And it must still refuse an invalid destination before touching RPCs.
raises("signed engine still rejects a bad destination",
        lambda: eng2.submit("0xdead", 1.0), ValueError)

# ── confirm() validates its input before polling ────────────────
raises("confirm rejects a non-signature", lambda: eng.confirm("0x" + "a" * 64),
        ValueError)
raises("confirm rejects an address as a tx", lambda: eng.confirm(GOOD), ValueError)

# ── repeated reads must not die on a stale event loop ───────────
# The first version of this engine cached one AsyncClient across calls
# while _run() starts a fresh loop per call. The first read worked and
# the second raised "Event loop is closed" -- which in production looks
# like "balances load once, then break forever". Exercise consecutive
# reads explicitly.
A = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"
try:
    b1 = eng.balance(A)
    b2 = eng.balance(A)
    chk("two consecutive balance() calls both work",
        b1["settled"] == b2["settled"],
        "settled=%s twice" % b1["settled"])
    chk("balance reports native SOL alongside USDC",
        "native_balance" in b1 and "native_symbol" in b1,
        "%.4f %s" % (b1.get("native_balance", -1), b1.get("native_symbol")))
    chk("balance reports pending separately from settled",
        "pending" in b1 and "pending_units" in b1,
        "pending=%s" % b1.get("pending"))
except Exception as e:
    chk("two consecutive balance() calls both work", False,
        type(e).__name__ + ": " + str(e)[:80])

# And a third, mixing in another method that also talks to the RPC.
try:
    eng.sol_balance(A)
    chk("sol_balance after balance() still works", True)
except Exception as e:
    chk("sol_balance after balance() still works", False,
        type(e).__name__ + ": " + str(e)[:80])

print("=" * 84)
print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
print("ALL ENGINE TESTS PASSED" if all(RESULTS) else "SOME ENGINE TESTS FAILED")
sys.exit(0 if all(RESULTS) else 1)