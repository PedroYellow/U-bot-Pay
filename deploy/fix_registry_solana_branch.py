#!/usr/bin/env python3
"""Make a registry's Solana branch honest about a missing Solana engine.

chains/registry.py is shared code with a branch per chain kind:

    if kind == "solana":
        from .solana import SolanaEngine
        from .solana_signer import FileSigner, NullSigner

In a whitelabel that is not Solana - Sonic, for one - the solana and
solana_signer modules were removed but the branch stayed. So
build_engine for a solana profile died with:

    ModuleNotFoundError: No module named 'chains.solana'

which reads like a broken deployment rather than "this deployment does
not support Solana".

The branch should say so. A deployment that does not have the engine
cannot have the chain, and saying that plainly is more useful than an
ImportError from three frames down.

The EVM and Tron branches are untouched - they are the deployment's
actual chains.

Usage: fix_registry_solana_branch.py <registry.py>
"""
import re
import sys


def fix(src):
    # 1. the _sdk_for branch
    old_sdk = '''    if kind == "solana":
        # solana-py and solders. They do not conflict with web3 or
        # tronpy at the eth-abi level -- the pin clash is between web3
        # and tronpy -- but a deployment still runs one chain, so the
        # conflict table stays as it is.
        return "solana"
    raise NotSupported(f"unknown chain kind: {kind!r}")'''

    new_sdk = '''    if kind == "solana":
        if not _has_solana():
            raise NotSupported(
                "this deployment has no Solana engine; it runs "
                f"{_deployed_kinds()}")
        # solana-py and solders. They do not conflict with web3 or
        # tronpy at the eth-abi level -- the pin clash is between web3
        # and tronpy -- but a deployment still runs one chain, so the
        # conflict table stays as it is.
        return "solana"
    raise NotSupported(f"unknown chain kind: {kind!r}")'''

    # 2. the build_engine branch - the import becomes conditional
    old_build = '''    if kind == "solana":
        # Solana is native: ed25519 keys and base58 addresses, so the
        # engine takes a signer object rather than a private_key_hex.
        # The key never enters this function -- the caller decides
        # whether a signer exists (read-only replica vs funded treasury).
        from .solana import SolanaEngine
        from .solana_signer import FileSigner, NullSigner
'''

    new_build = '''    if kind == "solana":
        if not _has_solana():
            raise NotSupported(
                "this deployment has no Solana engine; it runs "
                f"{_deployed_kinds()}")
        # Solana is native: ed25519 keys and base58 addresses, so the
        # engine takes a signer object rather than a private_key_hex.
        # The key never enters this function -- the caller decides
        # whether a signer exists (read-only replica vs funded treasury).
        from .solana import SolanaEngine
        from .solana_signer import FileSigner, NullSigner
'''

    # 3. the helpers
    helper = '''

def _has_solana() -> bool:
    """Whether this deployment has the Solana engine at all.

    The whitelabels are forks of the Solana deployment and they do not
    all keep the Solana modules. A deployment that removed them cannot
    serve a Solana profile, and should say so rather than failing with
    an ImportError from inside an import.
    """
    import importlib.util
    try:
        return (importlib.util.find_spec("chains.solana") is not None
                and importlib.util.find_spec("chains.solana_signer") is not None)
    except (ImportError, ValueError):
        return False


def _deployed_kinds() -> str:
    """The chain kinds this deployment can actually build."""
    kinds = []
    for kind, module in (("evm", "chains.evm"), ("tron", "chains.tron"),
                         ("solana", "chains.solana")):
        import importlib.util
        try:
            if importlib.util.find_spec(module) is not None:
                kinds.append(kind)
        except (ImportError, ValueError):
            pass
    return ", ".join(kinds) if kinds else "nothing"

'''

    out = src
    n = 0
    if old_sdk in out:
        out = out.replace(old_sdk, new_sdk, 1)
        n += 1
    if old_build in out:
        out = out.replace(old_build, new_build, 1)
        n += 1

    if n and "_has_solana" not in out.split("def _sdk_for")[0]:
        # put the helpers before _sdk_for
        marker = "def _sdk_for(spec: dict) -> str:"
        i = out.find(marker)
        if i != -1:
            out = out[:i] + helper.strip("\n") + "\n\n\n" + out[i:]

    return out, n


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/chains/registry.py"
    src = open(path, encoding="utf-8").read()
    out, n = fix(src)
    if n:
        open(path, "w", encoding="utf-8").write(out)
        print(f"{path}: {n} branch(es) made honest")
    else:
        print(f"{path}: nothing to fix")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
