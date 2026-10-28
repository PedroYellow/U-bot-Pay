#!/usr/bin/env python3
"""Make a deployment's default profile name its own.

chains/registry.py had this:

    def active_profile_name() -> str:
        return (cfg("PROFILE")
                or os.environ.get("UTRON_PROFILE")
                or "usolpay")

The literal "usolpay" is the Solana deployment's profile name. Every
other deployment inherited it, so whenever the PROFILE env var was not
read - a test, a script run without the env file, a config carried
over from another deployment - the service looked for a profile that
does not exist in that deployment, or worse, one that does and is the
wrong chain.

The default is now the deployment's own profile name, taken from the
env prefix this deployment uses. The prefix is the same one the rest
of the config uses, so it is already known to the module.

USOL_PROFILE for usolpay, USONIC_PROFILE for usonicpay, UTRON_PROFILE
for utronpay - the name follows the prefix, which is the rule the
deployment already follows for every other setting.

Usage: fix_default_profile_name.py <registry.py> <prefix>
"""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/chains/registry.py"
prefix = sys.argv[2] if len(sys.argv) > 2 else "USONIC"

src = open(path, encoding="utf-8").read()

OLD = '''    return (cfg("PROFILE")
            or os.environ.get("UTRON_PROFILE")
            or "usolpay")'''

NEW = '''    env_prefix = os.environ.get("UBOT_ENV_PREFIX") or "{prefix}"
    return (cfg("PROFILE")
            or os.environ.get(env_prefix + "_PROFILE")
            or os.environ.get("UTRON_PROFILE")
            or env_prefix[4:].lower())'''.replace("{prefix}", prefix)

if OLD not in src:
    print("  the fallback was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)

# the docstring should say what it now does
src = src.replace(
    """    The env var is USOL_PROFILE for USolPay. The old Utron_PROFILE
    name is still honoured so a config carried over from the Tron
    deployment keeps working, but the default is this deployment's
    own profile -- defaulting to another deployment's profile is how
    a service ends up reading the wrong chain.
    \"\"\"""",
    """    The env var is this deployment's own: USONIC_PROFILE for
    usonicpay, USOL_PROFILE for usolpay. The old Utron_PROFILE name is
    still honoured so a config carried over from the Tron deployment
    keeps working, but the default is this deployment's own profile.

    The default used to be the literal "usolpay" - the Solana
    deployment's profile name - in every deployment. Whenever the
    PROFILE env var was not read, a service looked for a profile that
    did not exist in it, or one that existed and was the wrong chain.
    \"\"\"""")

open(path, "w", encoding="utf-8").write(src)
print(f"{path}: the default profile name is now this deployment's own")
