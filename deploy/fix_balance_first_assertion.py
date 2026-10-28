#!/usr/bin/env python3
"""Make the "token with a balance is named first" assertion numeric.

The assertion was:

    chk("the token with a balance is named first", "0 " not in first, first)

It checks the STRING for "0 ". The reply is "0.0 S" - a zero balance
- and "0.0 S" contains "0 " at index 2, because the decimal point
sits between them. So the assertion failed on a token that IS at
zero, for a string reason rather than a numeric one.

The intent is "the first token named must hold something", so the
check is on the number: parse the figure out of the token and ask
whether it is non-zero.

With both balances at zero - which is the state of an unfunded
treasury - there is no token holding something, so the assertion
cannot be satisfied by any ordering. It is only meaningful when
something holds a balance, and it is checked the same way.

Usage: fix_balance_first_assertion.py <test.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/test_bot_flow.py"

src = open(path, encoding="utf-8").read()

OLD = '''        chk("the token with a balance is named first", "0 " not in first,
            first)'''

NEW = '''        # The first token named must hold something. The check is on
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

if OLD not in src:
    print("  the assertion was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
open(path, "w", encoding="utf-8").write(src)
print(f"{path}: the assertion now checks the number")
