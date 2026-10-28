#!/usr/bin/env python3
"""Derive a deployment's profile name from its env prefix, correctly.

The prefix and the profile name are not a simple suffix:

    DEPLOY_PREFIX=USONIC   ->  the profile is usonicpay.yaml
    DEPLOY_PREFIX=USOL     ->  the profile is usolpay.yaml
    DEPLOY_PREFIX=USTART   ->  the profile is ustartkpay.yaml

Deriving "USONIC"[4:] gives "IC", and the deployment went looking for
ic.yaml. The mapping is a name, not an arithmetic operation.

The profile name comes from the deployment itself: the file that is
already in its profiles/ directory. The prefix only picks WHICH file,
and the candidates are the profiles that exist.

If the deployment has exactly one profile, that is the one - no name
derivation needed at all, and nothing to get wrong. If it has more,
the prefix is matched against the file names.

Usage: fix_profile_name_derivation.py <registry.py> <profile-dir>
"""
import os
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/chains/registry.py"
profile_dir = sys.argv[2] if len(sys.argv) > 2 else "/opt/usonicpay/profiles"

src = open(path, encoding="utf-8").read()

OLD = '''    env_prefix = os.environ.get("UBOT_ENV_PREFIX") or "USONIC"
    return (cfg("PROFILE")
            or os.environ.get(env_prefix + "_PROFILE")
            or os.environ.get("UTRON_PROFILE")
            or env_prefix[4:].lower())'''

NEW = '''    env_prefix = os.environ.get("UBOT_ENV_PREFIX") or "USONIC"
    explicit = (cfg("PROFILE")
                or os.environ.get(env_prefix + "_PROFILE")
                or os.environ.get("UTRON_PROFILE"))
    if explicit:
        return explicit
    return _default_profile_name(env_prefix)


def _default_profile_name(env_prefix: str) -> str:
    """This deployment's own profile name.

    The name is not derivable from the prefix by arithmetic:
    USONIC -> usonicpay, not "onic". The deployment's profiles
    directory knows the real name, so it is read from there.

    With one profile, that is the answer. With several, the prefix is
    matched against them.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    profiles_dir = os.path.join(here, "profiles")
    try:
        names = [f[:-5] for f in os.listdir(profiles_dir)
                 if f.endswith(".yaml") and not f.startswith(".")]
    except OSError:
        names = []

    if len(names) == 1:
        return names[0]

    # match the prefix against the profile names, loosely: USONIC
    # against usonicpay, USTART against ustartkpay.
    p = env_prefix.lower().replace("pay", "")
    for n in names:
        nn = n.lower().replace("pay", "")
        if nn == p or nn.startswith(p) or p.startswith(nn):
            return n
    if names:
        return names[0]
    return env_prefix.lower()'''

if OLD not in src:
    print("  the derivation was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
open(path, "w", encoding="utf-8").write(src)
print(f"{path}: the profile name now comes from the deployment's profiles directory")
