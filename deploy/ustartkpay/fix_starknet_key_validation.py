#!/usr/bin/env python3
"""Make the Starknet engine validate the key before claiming a signer.

The bug

    has_signer() -> True
    a key loads   -> True

on a MAINNET deployment whose key file held a SEPOLIA account. The
engine loaded the key without checking two things:

  1. the network the key belongs to
  2. the address the key derives

So the deployment advertised a signer it could not use. A send would
have either failed obscurely or, worse, signed for an account that is
not the one the dashboard publishes.

Why it went unnoticed

has_signer() only checked that the account_address was non-empty, and
_key_pair() returned a KeyPair for any file with a private_key and a
public_key in it. Neither looked at the file's `network` field, and
neither compared the file's `address` against the deployment's
account_address.

The fix

_key_pair() now validates, and returns None when the key does not
belong to this deployment:

  - the file's network must match the deployment's network
  - the file's address must match the deployment's account_address
  - the address must be re-derived from the key and must still match
    (which catches a hand-edited file)

has_signer() then reports False, and submit() refuses with a clear
reason instead of attempting a transaction that cannot work.

The validation happens ONCE and is cached, so it does not re-read the
file on every send. And the file is only read if it is readable and
mode 0600 - a key file with loose permissions is a key file that has
been exposed.

Usage: fix_starknet_key_validation.py <engine.py>
"""
import re
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "/data/workspace/ubotpay/deploy/ustartkpay/chains_starknet_engine.py"

OLD_KEYPAIR = '''    def _key_pair(self):
        """The account's KeyPair, if this deployment has one.

        The key is read from a file, never from the environment - an
        env var is visible in /proc/<pid>/environ, in `ps eww` and in
        crash dumps. The file is mode 0600.
        """
        if not self._key_file:
            return None
        try:
            from starknet_py.net.signer.key_pair import KeyPair
            import json
            with open(self._key_file, encoding="utf-8") as fh:
                d = json.load(fh)
            return KeyPair(int(d["private_key"], 16), d["public_key"])
        except Exception:
            return None'''

NEW_KEYPAIR = '''    def _key_pair(self):
        """The account's KeyPair, if this deployment has one.

        The key is read from a file, never from the environment - an
        env var is visible in /proc/<pid>/environ, in `ps eww` and in
        crash dumps. The file is mode 0600.

        It is VALIDATED before it is returned. The bug this fixes: the
        mainnet deployment pointed at a SEPOLIA account file and
        has_signer() said True, because the old version loaded any
        file that had a private_key in it and never asked whether the
        key belonged to this network or to this address. A deployment
        that advertises a signer it cannot use is worse than one that
        says it has none.

        So the key must agree with the deployment on three counts:

          - the NETWORK: the file's "network" against this deployment's
          - the ADDRESS: the file's "address" against account_address
          - the DERIVATION: the address re-derived from the key

        The derivation check catches a hand-edited file, which the
        other two do not.

        The result is cached, so the file is read once and not on every
        send. And the file must be mode 0600: a key file with loose
        permissions is a key file that has been exposed.
        """
        if self._key_pair_cache is not _UNSET:
            return self._key_pair_cache
        self._key_pair_cache = None
        if not self._key_file:
            return None
        try:
            import json
            import os
            from starknet_py.net.signer.key_pair import KeyPair

            # the permissions check. A key file that anyone can read
            # is a key file that has already been exposed.
            mode = os.stat(self._key_file).st_mode & 0o777
            if mode & 0o077:
                log.warning(
                    "starknet: the key file %s is mode %o; it should be "
                    "0600. Not using it.", self._key_file, mode)
                return None

            with open(self._key_file, encoding="utf-8") as fh:
                d = json.load(fh)

            kp = KeyPair(int(d["private_key"], 16), d["public_key"])

            # 1. the network
            file_net = str(d.get("network") or "").lower()
            want_net = str(self.network or "").lower()
            if file_net and want_net and file_net != want_net:
                log.error(
                    "starknet: the key file is for %s but this deployment "
                    "is %s. Not using it.", file_net, want_net)
                return None

            # 2. the address
            file_addr = str(d.get("address") or "").lower()
            want_addr = str(self._account_address or "").lower()
            if file_addr and want_addr and file_addr != want_addr:
                log.error(
                    "starknet: the key file's address does not match this "
                    "deployment's account_address. Not using it.")
                return None

            # 3. the derivation - catches a hand-edited file
            try:
                from starknet_py.hash.address import compute_address
                derived = compute_address(
                    salt=int(str(d.get("salt") or "0x0"), 16),
                    class_hash=int(str(d.get("class_hash") or "0x0"), 16),
                    constructor_calldata=[kp.public_key],
                    deployer_address=0,
                )
                if file_addr and hex(derived).lower().lstrip("0x").rjust(
                        1, "0") != file_addr.lstrip("0x"):
                    log.error(
                        "starknet: the key file's address does not match "
                        "the address derived from its key. Not using it.")
                    return None
            except Exception:
                # if the file has no class_hash or salt, the derivation
                # cannot be checked - the other two checks still stand
                pass

            self._key_pair_cache = kp
            return kp
        except Exception:
            return None'''

OLD_HS = '''    def has_signer(self) -> bool:
        """Whether an account exists.

        A read-only deployment does not have one, and saying so plainly
        is more useful than a warning about an env var that by design
        never holds the key.
        """
        return bool(self._account_address)'''

NEW_HS = '''    def has_signer(self) -> bool:
        """Whether this deployment can actually sign.

        A read-only deployment does not have an account, and saying so
        plainly is more useful than a warning about an env var that by
        design never holds the key.

        The account address alone is not enough. The key has to be
        there AND it has to belong to this deployment - same network,
        same address. So this asks for the KeyPair, not for the
        address: a key file for another network makes the deployment
        read-only, which is what it is.
        """
        if not self._account_address:
            return False
        return self._key_pair() is not None'''


def main():
    src = open(PATH, encoding="utf-8").read()
    n = 0

    if OLD_KEYPAIR in src:
        src = src.replace(OLD_KEYPAIR, NEW_KEYPAIR, 1)
        n += 1
        print("  _key_pair() now validates the key")
    else:
        print("  _key_pair() was not found in the expected form")
        sys.exit(1)

    if OLD_HS in src:
        src = src.replace(OLD_HS, NEW_HS, 1)
        n += 1
        print("  has_signer() now asks for the key")
    else:
        print("  has_signer() was not found in the expected form")
        sys.exit(1)

    # the constructor needs a network and the cache slot
    OLD_INIT = """        self._key_file = key_file or ""
        self._timeout = timeout"""
    NEW_INIT = """        self._key_file = key_file or ""
        # The network this deployment serves. The key file names its
        # own network, and the two have to agree.
        self.network = network or "mainnet"
        self._timeout = timeout"""
    if OLD_INIT in src:
        src = src.replace(OLD_INIT, NEW_INIT, 1)
        n += 1
        print("  the constructor takes a network")
    else:
        print("  the constructor's key_file assignment was not found")

    OLD_SIG = """                 account_address: str | None = None,
                 key_file: str | None = None,"""
    NEW_SIG = """                 account_address: str | None = None,
                 key_file: str | None = None,
                 network: str = "mainnet","""
    if OLD_SIG in src:
        src = src.replace(OLD_SIG, NEW_SIG, 1)
        n += 1
    else:
        print("  the constructor's signature was not found")

    # the cache slot, next to the other private attributes
    OLD_SLOT = """        self._client = None
        self._decimals = None
        self._fee_decimals = None"""
    NEW_SLOT = """        self._client = None
        self._decimals = None
        self._fee_decimals = None
        # The validated KeyPair, cached. _UNSET means "not read yet",
        # so a failed read is not retried on every send.
        self._key_pair_cache = _UNSET"""
    if OLD_SLOT in src:
        src = src.replace(OLD_SLOT, NEW_SLOT, 1)
        n += 1
        print("  the key cache slot was added")
    else:
        print("  the private attribute block was not found")

    # the sentinel, next to the other module constants
    OLD_CONST = "STRK = "
    NEW_CONST = ("# The sentinel for \"the key has not been read yet\". None is a\n"
                 "# real value here - it means the key was checked and rejected.\n"
                 "_UNSET = object()\n\n"
                 "STRK = ")
    if OLD_CONST in src and "_UNSET = object()" not in src:
        src = src.replace(OLD_CONST, NEW_CONST, 1)
        n += 1
        print("  the sentinel was added")
    else:
        print("  the constants block was not found")

    open(PATH, "w", encoding="utf-8").write(src)

    try:
        compile(src, PATH, "exec")
        print(f"{PATH}: {n} change(s), it parses")
    except SyntaxError as e:
        print(f"{PATH}: {n} change(s) but it does NOT parse: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
