#!/usr/bin/env python3
"""Update the repo's ustartkpay nginx vhost to the native Starknet port.

The live vhost on the VPS was already updated to point at the native
Starknet service on 127.0.0.1:8095 - but the repository's template
still said 8090, which is the AVALANCHE deployment. So the repo
drifted from the server, and the drift check caught it.

The ONLY difference between the two files was the port - 25
references, all of them the backend - so this is a port swap, not a
merge, and nothing else in the live file is imported blind.

It also fixes the header comment, which was left saying "Same backend
as ubotpay (127.0.0.1:8090)". That was true when the vhost was
written and it stopped being true when the native Starknet service
went up on 8095. A comment that describes a backend the file does not
use is worse than no comment: the next person to read it believes
ustartkpay is served by the Avalanche app.

Usage: update_ustartkpay_vhost.py <repo-vhost> <live-vhost>
"""
import sys

REPO = sys.argv[1] if len(sys.argv) > 1 else \
    "/data/workspace/ubotpay/deploy/nginx-vhosts/ustartkpay.conf"
LIVE = sys.argv[2] if len(sys.argv) > 2 else \
    "/data/cache/scratch/live_ustartkpay.conf"

OLD_HEADER = """# U-bot Pay — ustartkpay.com whitelabel
# Same backend as ubotpay (127.0.0.1:8090), separate hostname, separate
# rate-limit buckets and its own CORS origin. Rate-limit zone names are
# global in nginx, so they must differ from the ubotpay ones."""

NEW_HEADER = """# U-bot Pay — ustartkpay.com whitelabel
# Its OWN backend: the native Starknet service on 127.0.0.1:8095.
# Not the Avalanche one on 8090 - Starknet is not EVM, it needs the
# starknet engine. Separate hostname, separate rate-limit buckets and
# its own CORS origin. Rate-limit zone names are global in nginx, so
# they must differ from the ubotpay ones."""

PORT_OLD = "8090"
PORT_NEW = "8095"


def main():
    src = open(REPO, encoding="utf-8").read()
    n = src.count(f"127.0.0.1:{PORT_OLD}")

    src = src.replace(f"127.0.0.1:{PORT_OLD}", f"127.0.0.1:{PORT_NEW}")

    if OLD_HEADER in src:
        src = src.replace(OLD_HEADER, NEW_HEADER, 1)
        print("  the header comment was corrected")
    else:
        print("  the header comment was not found in the expected form")

    open(REPO, "w", encoding="utf-8").write(src)
    print(f"  {n} backend reference(s) moved {PORT_OLD} -> {PORT_NEW}")

    # verify it now matches the live file byte for byte
    live = open(LIVE, encoding="utf-8").read()
    if src == live:
        print("  it now matches the live vhost exactly")
    else:
        # the live file still has the old header comment, so compare
        # everything except that
        import difflib
        d = list(difflib.unified_diff(src.splitlines(), live.splitlines(),
                                      lineterm="", n=0))
        real = [l for l in d if l.startswith(("+", "-"))
                and not l.startswith(("+++", "---"))]
        if not real:
            print("  it matches the live vhost (modulo the header comment)")
        else:
            print(f"  {len(real)} line(s) still differ from the live vhost:")
            for l in real[:8]:
                print(f"    {l}")


if __name__ == "__main__":
    main()
