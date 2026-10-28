"""Prefix-aware configuration for a whitelabel deployment.

Every deployment used to read its settings from hardcoded names like
`USOL_WALLET_ADDRESS`. That works for exactly one deployment per
codebase, and the whitelabels are separate copies of this code, so each
copy needed its own rename - 101 places in the Solana one, and the next
chain would need the same again.

This module is the one place that knows the prefix. The rest of the code
calls `cfg("WALLET_ADDRESS", default)` instead of
`os.environ.get("USOL_WALLET_ADDRESS", "")`.

The prefix comes from the environment itself, from `USOL_PROFILE`-style
`DEPLOY_PREFIX`. It defaults to `USOL` so an existing deployment keeps
working with no change.

    DEPLOY_PREFIX=USONIC          -> USONIC_WALLET_ADDRESS
    DEPLOY_PREFIX=USTART          -> USTART_WALLET_ADDRESS
    (unset)                       -> USOL_WALLET_ADDRESS

A deployment can also set any variable with no prefix at all, which is
how shared values like ELEVENLABS_API_KEY are passed to every
deployment. The prefixed name wins when both are present.
"""
import os

# The prefix for this deployment. Set once, at import.
def _prefix() -> str:
    """This deployment's env prefix, read on each use.

    It used to be a module constant, computed once at import time.
    That made the module order-dependent: anything that imported config
    before the deployment's .env was loaded froze the prefix as the
    default, and every lookup went to the wrong variable name. A test
    reading its own profile did exactly that, and the service then
    asked for USOL_API_KEY in a deployment that reads USONIC_API_KEY.
    """
    return os.environ.get("DEPLOY_PREFIX", "USOL").strip().upper()


class _LazyPrefix(str):
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


def set_prefix(prefix: str) -> None:
    """Point this deployment's config at a prefix, and keep it."""
    os.environ["DEPLOY_PREFIX"] = prefix.strip().upper()
    global PREFIX
    PREFIX = _LazyPrefix()

# Names that are genuinely shared between deployments, so they are read
# without a prefix. The prefixed form still works and takes priority.
SHARED = {
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_STT_MODEL",
    "ELEVENLABS_TTS_MODEL",
    "ELEVENLABS_VOICE_ID",
}


def full_name(name: str) -> str:
    """The prefixed environment name for a setting."""
    if name in SHARED:
        return name
    return f"{_prefix()}_{name}"


def cfg(name: str, default: str | None = None) -> str | None:
    """Read a setting.

    The prefixed name wins. If it is absent, the unprefixed name is
    tried - which is what lets a shared secret be set once for every
    deployment on the host. Returns `default` when neither is set.
    """
    prefixed = full_name(name)
    val = os.environ.get(prefixed)
    if val is not None and val != "":
        return val
    if name in SHARED:
        return os.environ.get(name, default)
    # An unprefixed fallback for names that are not explicitly shared:
    # it lets a value be inherited from the parent environment without
    # every deployment having to restate it.
    val = os.environ.get(name)
    if val is not None and val != "":
        return val
    return default


def cfg_int(name: str, default: int = 0) -> int:
    try:
        val = cfg(name, str(default))
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def cfg_float(name: str, default: float = 0.0) -> float:
    try:
        val = cfg(name, str(default))
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def cfg_bool(name: str, default: bool = False) -> bool:
    val = cfg(name, "")
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def pop_cfg(name: str, default=None):
    """Remove a setting and return what it held.

    A test needs this to prove that the default applies when a setting
    is absent: it removes the variable, runs the code, and checks the
    fallback. Removing the prefixed name alone would leave the
    unprefixed fallback in place and the test would prove nothing.
    """
    prefixed = full_name(name)
    val = os.environ.pop(prefixed, None)
    if name in SHARED:
        other = os.environ.pop(name, None)
        return other if other is not None else default
    other = os.environ.pop(name, None)
    return val if val is not None else (other if other is not None else default)


def set_cfg(name: str, value) -> None:
    """Write a setting under the deployment's prefix."""
    os.environ[full_name(name)] = str(value)


def del_cfg(name: str) -> None:
    """Remove a setting, both the prefixed and the unprefixed form."""
    os.environ.pop(full_name(name), None)
    if name not in SHARED:
        os.environ.pop(name, None)
