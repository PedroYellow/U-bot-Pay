#!/usr/bin/env python3
"""Make the concurrent-confirm assertion match what it can prove.

The assertion was:

    settled = results.count("settled")
    chk("four concurrent confirms settle exactly once", settled == 1, ...)
    chk("the other three confirms do not settle", settled == 1 and len(results) == 4, ...)

The thing it actually guards is the DOUBLE SETTLEMENT: four confirms
racing on one pending payment must not send four payments.

`_pending.pop(uid, None)` is atomic, so exactly one thread takes the
pending payment and the other three find nothing - their action is
"none". That is the guard working.

But the test asserted the action was "settled", and "settled" means
the payment was SENT ON CHAIN. On a deployment whose treasury holds
nothing, the send cannot succeed, so the winning thread's action is
"failed" - and the test reported that as a failure of the guard.

So the assertion now checks what the guard is actually about:

  - exactly one thread took the pending payment (one outcome that is
    not "none")
  - the other three found nothing to confirm

and it reports separately whether the payment actually settled, which
depends on the treasury being funded.

Usage: fix_concurrent_confirm_assertion.py <test.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/test_audit_findings.py"

src = open(path, encoding="utf-8").read()

OLD = '''    settled = results.count("settled")
    chk("four concurrent confirms settle exactly once", settled == 1,
        "%d settled de %d: %s" % (settled, len(results), results))
    chk("the other three confirms do not settle",
        settled == 1 and len(results) == 4, results)'''

NEW = '''    # The guard is against DOUBLE SETTLEMENT: four confirms racing on
    # one pending payment must not send four payments.
    #
    # `_pending.pop(uid, None)` is atomic, so exactly one thread takes
    # the pending payment and the other three find nothing - their
    # action is "none". That is the guard working.
    #
    # The outcome of the winning thread depends on the treasury: with
    # funds it settles, without them the send fails. The assertion
    # used to require "settled", so on an unfunded deployment it
    # reported the guard as broken when the guard had held.
    settled = results.count("settled")
    failed = results.count("failed")
    nothing = results.count("none")
    took_it = settled + failed

    chk("exactly one confirm took the pending payment", took_it == 1,
        "%d settled, %d failed, %d found nothing: %s"
        % (settled, failed, nothing, results))
    chk("the other three confirms did not take it",
        nothing == 3 and len(results) == 4, results)
    # Informational, not a failure: it needs a funded treasury.
    print("  note: %d settled, %d failed (a failed send means the "
          "treasury cannot pay)" % (settled, failed))'''

if OLD not in src:
    print("  the assertion was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
open(path, "w", encoding="utf-8").write(src)
print(f"{path}: the assertion now checks the double-settlement guard")
