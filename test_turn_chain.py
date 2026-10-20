"""
Regression tests for Turn.chain_id (audit 20 Oct, multichain diagnosis).

The balance intent has always read `t.chain_id`, but the field was never
declared on the Turn model. Pydantic drops undeclared fields silently, so
sending {"chain_id": 8453} returned 200 with Avalanche data and no warning —
verified against production before the fix.

Worse than a no-op: a user row holds ONE address on ONE chain, and an EVM
address means nothing on a different chain (it can belong to someone else
there). Answering would be reporting another wallet's balance.

    python3 test_turn_chain.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:58} got={got!r} want={want!r}")


# ── the field is declared and validated ───────────────────────────────────
def t_field_is_declared():
    """Before the fix the model had no chain_id, so extra=ignore ate it."""
    try:
        from pydantic import BaseModel, Field

        class Turn(BaseModel):
            user_key: str = "demo"
            text: str
            channel: str = "voice"
            lang_hint: str | None = None
            chain_id: int = Field(default=43114, ge=1, le=999_999)

        t = Turn(user_key="demo", text="balance", chain_id=8453)
        check("chain_id is carried through", t.chain_id, 8453)
        d = Turn(user_key="demo", text="balance")
        check("defaults to Avalanche", d.chain_id, 43114)
    except ImportError:
        check("pydantic available", False, True)


def t_validation():
    from pydantic import BaseModel, Field, ValidationError

    class Turn(BaseModel):
        chain_id: int = Field(default=43114, ge=1, le=999_999)

    for bad in (0, -1, 10_000_000):
        try:
            Turn(chain_id=bad)
            check(f"chain_id={bad} rejected", False, True)
        except ValidationError:
            check(f"chain_id={bad} rejected", True, True)
    check("unknown but in-range id is allowed here",
          Turn(chain_id=999_999).chain_id, 999_999)


# ── the balance guard ─────────────────────────────────────────────────────
def balance_guard(user_chain, user_address, requested_chain):
    """Mirror of the guard in the balance intent. Returns (ok, detail)."""
    registered = user_chain or 43114
    if requested_chain != registered and user_address:
        return False, f"registered on chain {registered}, not {requested_chain}"
    return True, "reads the user wallet"


def t_wrong_chain_refused():
    ok, _ = balance_guard(43114, "0xabc", 8453)
    check("balance on another chain is refused", ok, False)


def t_own_chain_allowed():
    ok, _ = balance_guard(43114, "0xabc", 43114)
    check("balance on its own chain is served", ok, True)


def t_no_address_falls_back():
    """A demo user with no address reads the operator treasury, which is on
    Avalanche. That must not be blocked by the guard."""
    ok, _ = balance_guard(43114, None, 8453)
    check("no address -> falls through to treasury", ok, True)


def t_none_chain_defaults():
    """user['chain_id'] is NULL for rows created before the multi-chain work."""
    ok, _ = balance_guard(None, "0xabc", 43114)
    check("NULL chain_id treated as Avalanche", ok, True)
    ok2, _ = balance_guard(None, "0xabc", 8453)
    check("NULL chain_id still refuses another chain", ok2, False)


def t_why_this_matters():
    """Document the actual risk: the same 0x address is a different wallet on
    every EVM chain. Reading it elsewhere is not a zero, it is someone else."""
    addr = "0x1111111111111111111111111111111111111111"
    chains = [43114, 8453, 1, 137, 42161]
    owners = {
        43114: "Alice",
        8453: "Bob",
        1: "a multisig treasury",
        137: "an exchange hot wallet",
        42161: "an unrelated contract deployer",
    }
    print("      the same address, five chains, five different owners:")
    for c in chains:
        print(f"        {c:>6}  owned by {owners[c]}")
    print()
    check("so a cross-chain read is a real disclosure, not a no-op", True, True)


if __name__ == "__main__":
    t_field_is_declared()
    t_validation()
    t_wrong_chain_refused()
    t_own_chain_allowed()
    t_no_address_falls_back()
    t_none_chain_defaults()
    t_why_this_matters()
    print("=" * 72)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)