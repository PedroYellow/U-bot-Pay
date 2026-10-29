#!/usr/bin/env python3
"""Stop the status pill saying "Tron Mainnet mainnet".

The pill is built like this:

    const chainName = (d.chain || "mainnet").replace(/-mainnet$/, "");
    n.textContent = "● " + chainName + " mainnet · " + I18N.t("live");

The API returns the chain name in its own spelling:

    api.ubotpay.com      'avalanche-mainnet'
    api.usolpay.com      'USolPay'
    api.usonicpay.com    'Sonic'
    api.ustartkpay.com   'Starknet'
    api.utronpay.com     'Tron Mainnet'

The replace only strips the hyphenated form, which is Avalanche's. So
Tron keeps its own "Mainnet" and the pill says:

    ● Tron Mainnet mainnet · live

Avalanche's own spelling is also a bit off - it reads "avalanche
mainnet" in lowercase - so this normalises the shape as well.

The pill should read:

    ● Avalanche mainnet · live
    ● USolPay mainnet · live
    ● Sonic mainnet · live
    ● Starknet mainnet · live
    ● Tron mainnet · live

The point of the pill is that it cannot lie about which chain the
service is on. It reads the chain from the API precisely so it cannot
drift - so it has to render it the same way for every chain.

Idempotent.

Usage: fix_chain_badge.py <html-file> [...]
"""
import os
import shutil
import sys

OLD = '      const chainName = (d.chain || "mainnet").replace(/-mainnet$/, "");'

NEW = '''      // The chain name comes from the API, so the pill cannot drift
      // from what the service is actually on.
      //
      // But the APIs spell it their own way - "avalanche-mainnet",
      // "USolPay", "Sonic", "Starknet", "Tron Mainnet" - so the
      // hyphenated form is stripped and any "Mainnet" the name
      // already carries is stripped too. Otherwise Tron read
      // "Tron Mainnet mainnet", and Avalanche read "avalanche
      // mainnet" in lowercase.
      //
      // And the first letter is capitalised, so Avalanche reads
      // "Avalanche" rather than "avalanche". A brand name in
      // lowercase is not a rendering detail - it is the name of a
      // chain, and the name of a chain is capitalised.
      const chainName = (d.chain || "mainnet")
        .replace(/-mainnet$/i, "")
        .replace(/\\s+mainnet$/i, "")
        .replace(/^\\s+|\\s+$/g, "")
        .replace(/^[a-z]/, (c) => c.toUpperCase());'''


def patch(text):
    if NEW in text:
        return text, False
    if OLD not in text:
        return None, False
    return text.replace(OLD, NEW, 1), True


def main():
    paths = sys.argv[1:]
    if not paths:
        print(__doc__)
        return 1

    changed = skipped = failed = 0
    for path in paths:
        if not os.path.exists(path):
            print(f"  {path}: does not exist")
            failed += 1
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        out, did = patch(src)
        if out is None:
            print(f"  {path}: the anchor was not found")
            failed += 1
            continue
        if not did:
            print(f"  {path}: already correct")
            skipped += 1
            continue
        shutil.copy2(path, path + ".bak-badge")
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"  {path}: the badge is normalised")
        changed += 1

    print(f"  {changed} patched, {skipped} already fine, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
