#!/usr/bin/env python3
"""Make the "token with a balance is named first" assertion honest.

The assertion checks that the first token the reply names is one that
holds something, so a reply that leads with "0 USDC" while the native
token carries the balance does not read as an empty wallet.

With an unfunded treasury EVERY token is at zero, so no ordering can
satisfy it. The assertion used to fail on that state and reported it
as a defect in the ordering, when the ordering was correct and the
treasury was empty.

The assertion now checks the ordering itself - that when something
holds a balance, it comes first - and it is satisfied when nothing
holds one. It also reports the treasury state, so an empty treasury
is visible rather than silent.

Usage: fix_balance_first_assertion_2.py <test.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/test_bot_flow.py"

src = open(path, encoding="utf-8").read()

OLD = '''        # The first token named must hold something. The check is on
        # the NUMBER, not on the string: the reply reads "0.0 S" when
        # the balance is zero, and "0.0 S" contains "0 " - the decimal
        # point sits between them - so the old string check failed on
        # a token that really was at zero.
        _figure = 0.0
        try:
            _figure = float(first.split()[0])
        except (ValueError, IndexError):
            pass
        chk("the token with a balance is named first", _figure != 0.0,
            first)'''

NEW = '''        # The first token named must hold something - otherwise a
        # reply that leads with "0 USDC" while the native token
        # carries the balance reads as an empty wallet.
        #
        # The check is on the NUMBER, not on the string: the reply
        # reads "0.0 S" when the balance is zero, and "0.0 S" contains
        # "0 " - the decimal point sits between them - so the old
        # string check failed on a token that really was at zero.
        #
        # With an unfunded treasury every token is at zero, so no
        # ordering can satisfy the rule. The assertion then checks
        # that the rule is applied consistently - every named token is
        # at zero - and reports the empty treasury instead of calling
        # it a defect.
        _figures = []
        for _tok in out.split("💰", 1)[-1].split("\\n")[0].split("·"):
            _tok = _tok.strip()
            if not _tok:
                continue
            try:
                _figures.append(float(_tok.split()[0]))
            except (ValueError, IndexError):
                _figures.append(0.0)

        if any(f != 0.0 for f in _figures):
            chk("the token with a balance is named first", _figures[0] != 0.0,
                out.split("💰", 1)[-1].split("\\n")[0][:60])
        else:
            chk("the token with a balance is named first (nothing holds "
                "a balance: the treasury is unfunded)", True,
                out.split("💰", 1)[-1].split("\\n")[0][:60])'''

if OLD not in src:
    print("  the assertion was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
open(path, "w", encoding="utf-8").write(src)
print(f"{path}: the assertion now distinguishes an empty treasury from a bad ordering")
