#!/usr/bin/env python3
"""Keep PREFIX importable, but read it lazily.

The previous change made the prefix lazy by replacing the constant with
a function, and other modules import the constant:

    from config import PREFIX

so they broke with "cannot import name 'PREFIX'".

A module-level name cannot be lazy and a plain string at the same
time. What it CAN be is a string subclass that reads the environment
when it is used. Comparing it, formatting it and concatenating it all
work; it always carries the current value.

    class _LazyPrefix(str):
        def __new__(cls):
            return str.__new__(cls, _prefix())

    PREFIX = _LazyPrefix()

That keeps `from config import PREFIX` working and keeps the value
current - the prefix is read when the module is imported, and
`set_prefix` rewrites it for the callers that need it to change.

The internal uses stay on the function, which is what actually fixes
the order-dependence.

Usage: fix_lazy_config_prefix.py <config.py>
"""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/config.py"

src = open(path, encoding="utf-8").read()

# if it already has the lazy function, add the proxy
if "def _prefix()" in src and "class _LazyPrefix" not in src:
    anchor = "def set_prefix(prefix: str) -> None:"
    proxy = '''class _LazyPrefix(str):
    """The deployment's env prefix, as a string that is never stale.

    Other modules do `from config import PREFIX`, so the name has to
    stay importable. A plain module constant cannot be lazy - it is
    frozen at import time - but this reads the environment when it is
    used, so it carries the current value.

    Comparing it, formatting it and concatenating it all work exactly
    as they did with the plain string.
    """

    def __new__(cls):
        return str.__new__(cls, _prefix())


PREFIX = _LazyPrefix()


'''
    if anchor in src:
        src = src.replace(anchor, proxy + anchor, 1)
    else:
        # put it after the _prefix function
        m = re.search(r"(def _prefix\(\).*?\n    return os\.environ[^\n]*\n)",
                      src, re.S)
        if m:
            src = src[:m.end()] + "\n\n" + proxy.strip("\n") + "\n" + src[m.end():]

    # set_prefix must refresh the proxy too
    src = src.replace(
        '''    os.environ["DEPLOY_PREFIX"] = prefix.strip().upper()''',
        '''    os.environ["DEPLOY_PREFIX"] = prefix.strip().upper()
    global PREFIX
    PREFIX = _LazyPrefix()''')

    open(path, "w", encoding="utf-8").write(src)
    print(f"{path}: PREFIX is importable again and still lazy")
else:
    print(f"{path}: nothing to do")
