#!/usr/bin/env python3
"""Test that the Starknet engine's client is never awaited.

The bug

    account = Account(client=await self.client, ...)

`client` is a PROPERTY that returns the FullNodeClient - not a
coroutine. Awaiting it raises:

    TypeError: object FullNodeClient can't be used in 'await' expression

and that happened INSIDE the try/except in submit(), so every Starknet
transfer failed with an error that named the wrong thing entirely. The
engine's own tests passed 31/31 anyway, because they never send.

The same mistake was in _confirm(), so the settlement check was broken
too.

Why a static test

The failure only shows up when a transaction is actually signed, and
the account has to be deployed and funded for that to get far enough
to matter. Neither is true on this deployment. So the check is
STATIC: every `await` in the engine is looked at, and any that awaits
`self.client` on its own - rather than awaiting a method call on it -
is a bug.

The distinction that matters:

    await self.client.call_contract(...)   <- correct, awaits the RESULT
    await self.client                      <- wrong, awaits the client

Usage: test_starknet_client_await.py
"""
import ast
import os
import sys

ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "chains_starknet_engine.py")

RESULTS = []


def chk(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print("%s  %-58s %s" % ("PASS" if cond else "FAIL", name, detail))


def main():
    if not os.path.exists(ENGINE):
        print("  the engine is missing:", ENGINE)
        return 1

    src = open(ENGINE, encoding="utf-8").read()
    tree = ast.parse(src)

    # find every `await X` and classify it
    bad = []
    good = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await):
            continue
        v = node.value

        # await self.client            -> Attribute(self, "client")
        # The client object itself is awaited. Wrong.
        if (isinstance(v, ast.Attribute) and v.attr == "client"
                and isinstance(v.value, ast.Name) and v.value.id == "self"):
            bad.append((node.lineno, "await self.client"))
            continue

        # await self.client.call_contract(...)
        #   -> Call(func=Attribute(value=Attribute(self, "client"), "call_contract"))
        # Awaiting the RESULT of a method call. Correct.
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute):
            inner = v.func.value
            if (isinstance(inner, ast.Attribute) and inner.attr == "client"
                    and isinstance(inner.value, ast.Name)
                    and inner.value.id == "self"):
                good.append((node.lineno, f"await self.client.{v.func.attr}(...)"))
                continue

        # await self.client() -> a Call on the property. Also wrong:
        # the property returns the client, it is not a coroutine.
        if (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                and v.func.attr == "client"):
            bad.append((node.lineno, "await self.client()"))

    print("=== the awaits on the client in the engine ===")
    for ln, what in sorted(good):
        print(f"  line {ln}: {what} - correct")
    for ln, what in sorted(bad):
        print(f"  line {ln}: {what} - WRONG")

    print()
    chk("no await on the client object itself", not bad,
        f"{len(bad)} bad" if bad else "clean")
    chk("the awaits on the method calls are intact", len(good) >= 2,
        f"{len(good)} found")

    print()
    print("=== the source text agrees ===")
    chk("no 'client=await self.client' in the source",
        "client=await self.client" not in src)
    chk("no 'client = await self.client' in the source",
        "client = await self.client" not in src)
    chk("'client=self.client' is present", "client=self.client" in src)

    print()
    passed = sum(1 for r in RESULTS if r)
    print(f"{passed}/{len(RESULTS)} passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
