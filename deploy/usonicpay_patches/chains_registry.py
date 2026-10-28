"""
Profile registry — chains as configuration.

A profile is a YAML file describing one deployment: which engine, which
chain, which token, what the fee and limits are, which Telegram bot and
which database it owns. Loading a profile builds exactly one engine and
refuses to build a second one whose SDK conflicts.

That refusal is the load-bearing part. U-bot Pay's payments were nearly
broken by `pip install tronpy` into its venv: tronpy pins eth-abi<6,
web3 pins eth-abi>=6, and pip downgraded eth-abi to satisfy the newcomer.
A profile registry that validated SDK compatibility up front would have
stopped it at deploy time instead of at payment time.

Adding Somnia tomorrow:
  profiles/somnia.yaml, ~12 lines, no Python. That is the whole change.
"""
import os
import logging

from .engine import ChainEngine, NotSupported, sdks_conflict
from config import PREFIX
from config import cfg, pop_cfg, set_cfg, del_cfg

log = logging.getLogger("utronpay.registry")

PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "profiles")


def _load_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError:
        # Minimal flat-YAML reader: profiles are simple scalars and one
        # nested map. Avoids making PyYAML a hard dependency (it was
        # removed from ubotpay's venv in audit round 21 as a dead dep).
        return _mini_yaml(path)
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _mini_yaml(path: str) -> dict:
    """Parse the subset of YAML the profile files use: `key: value` lines,
    indented sub-maps, `- item` sequences, and `#` comments.

    Written because PyYAML is not a dependency — it was removed from
    ubotpay's venv in audit round 21 as a dead weight, and adding it back
    to read config files would be worse. Only real profiles are ever
    parsed this way, so the subset is enough.

    Two passes, because guessing the container type while streaming is
    where this went wrong three times: `key:` alone does not say whether
    the value is a map or a sequence, and optimistically guessing one
    nests the other under the key's own name. Pass 1 groups lines by
    indent into a tree; pass 2 assigns a container per node with full
    knowledge of its children.
    """
    with open(path) as fh:
        raw_lines = fh.read().splitlines()

    # ── pass 1: lines → indent tree ──
    root = {"indent": -1, "lines": [], "children": []}
    stack = [root]
    for raw in raw_lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        node = {"indent": indent, "text": stripped, "children": []}
        while len(stack) > 1 and indent <= stack[-1]["indent"]:
            stack.pop()
        stack[-1]["children"].append(node)
        # A `- item` is a leaf; it never opens a block.
        if not (stripped == "-" or stripped.startswith("- ")):
            stack.append(node)

    def render(node) -> dict:
        out: dict = {}
        for child in node["children"]:
            text = child["text"]
            if text == "-" or text.startswith("- "):
                # Sequence items are consumed by their owning key below;
                # a stray one at this level is a malformed profile.
                continue
            key, _, val = text.partition(":")
            key, val = key.strip(), val.strip()
            if val != "":
                out[key] = _scalar(val)
                continue
            # `key:` with children. Decide map vs list by what is inside.
            has_seq = any(c["text"] == "-" or c["text"].startswith("- ")
                          for c in child["children"])
            if has_seq:
                items = []
                for c in child["children"]:
                    if c["text"].startswith("- "):
                        items.append(_scalar(c["text"][2:].strip()))
                    elif c["text"] == "-":
                        items.append(render(c))
                    else:
                        # A bare `key: value` sibling of the sequence: skip,
                        # it is not part of the list.
                        continue
                out[key] = items
            else:
                out[key] = render(child)
        return out

    return render(root)


def _scalar(val: str):
    """Coerce a YAML scalar: quoted → str, bool, int, float, else str."""
    if val.startswith(("'", '"')) and val.endswith(("'", '"')) and len(val) >= 2:
        return val[1:-1]
    if val.lower() in ("true", "false"):
        return val.lower() == "true"
    if val.lower() in ("null", "~", ""):
        return None
    if val.lstrip("-").isdigit():
        return int(val)
    try:
        return float(val)
    except ValueError:
        return val


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


def _sdk_for(spec: dict) -> str:
    """Which SDK a profile entry needs, without importing it.

    Kept in sync with the `kind` mapping in build_engine; it exists so the
    registry can reject an incompatible pairing before anything is
    constructed.
    """
    kind = (spec.get("kind") or spec.get("type") or "evm").lower()
    if kind == "tron":
        return "tronpy"
    if kind == "evm":
        return "web3"
    if kind == "solana":
        if not _has_solana():
            raise NotSupported(
                "this deployment has no Solana engine; it runs "
                f"{_deployed_kinds()}")
        # solana-py and solders. They do not conflict with web3 or
        # tronpy at the eth-abi level -- the pin clash is between web3
        # and tronpy -- but a deployment still runs one chain, so the
        # conflict table stays as it is.
        return "solana"
    raise NotSupported(f"unknown chain kind: {kind!r}")


def build_engine(spec: dict) -> ChainEngine:
    """Instantiate one engine from a profile's `chain:` block.

    Imports the SDK lazily and only for the engine actually requested —
    that is what keeps a Tron profile from ever importing web3.
    """
    kind = (spec.get("kind") or spec.get("type") or "evm").lower()
    if kind == "tron":
        from .tron import TronEngine
        return TronEngine(
            network=spec.get("network", "mainnet"),
            rpc=spec.get("rpc"),
            explorer=spec.get("explorer"),
            stable_address=spec.get("stable_address"),
            private_key_hex=spec.get("private_key"),
            revenue_address=spec.get("revenue_address"),
            api_key=spec.get("api_key"),
            fallback_rpcs=spec.get("fallback_rpcs") or [],
        )
    if kind == "evm":
        from .evm import EvmEngine
        return EvmEngine(
            chain_id=int(spec["chain_id"]),
            name=spec.get("name") or f"Chain {spec['chain_id']}",
            rpc=spec["rpc"],
            stable_address=spec["stable_address"],
            explorer=spec.get("explorer", ""),
            native_symbol=spec.get("native", "ETH"),
            # The key file wins over the environment value: the file is
            # the one that can be kept at 0600.
            key_path=spec.get("key_path"),
            private_key_hex=spec.get("private_key"),
            revenue_address=spec.get("revenue_address"),
            stable_symbol=spec.get("stable_symbol", "USDC"),
        )
    if kind == "solana":
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

        key_path = spec.get("key_path") or spec.get("private_key")
        signer = NullSigner()
        if key_path:
            # A missing key file must not stop the service from booting.
            # Before the treasury is funded there is nothing to sign,
            # and the read paths -- balances, health, quotes -- are
            # useful. The engine refuses sends with a message that says
            # which env var to set, so a missing key shows up as a
            # refused payment rather than a dead service.
            if os.path.exists(key_path):
                signer = FileSigner(key_path)
            else:
                log.warning(
                    "no Solana treasury key at %s; starting read-only",
                    key_path,
                )
        # `profile` may be a path to the profile file or an already
        # loaded dict. The path form is what the deployment uses: the
        # same file is the engine's source of truth for the mint, the
        # decimals and the RPC.
        prof = spec.get("profile") or {}
        if isinstance(prof, str):
            if os.path.isabs(prof):
                prof = load_profile(prof)
            elif prof.startswith(PROFILE_DIR.rstrip("/") + "/"):
                # Already a full path into the profiles directory.
                prof = load_profile(prof)
            else:
                prof = load_profile(os.path.join(PROFILE_DIR, prof))
        return SolanaEngine(
            profile=prof,
            signer=signer,
            spec=spec,
        )
    raise NotSupported(f"unknown chain kind: {kind!r}")


class Registry:
    """Holds the engines for one deployment.

    A deployment runs ONE chain. Multi-chain bridging in ubotpay is a
    bridge concern (LI.FI), not a treasury concern: the treasury holds
    one asset on one chain, and this keeps that invariant true.
    """

    def __init__(self, profile: dict):
        self.profile = profile
        self.engines: dict = {}
        self._load()

    def _load(self):
        specs = self.profile.get("chains")
        if isinstance(specs, dict):
            specs = [specs]
        if not specs:
            raise NotSupported("profile declares no chains")
        seen_sdks: set = set()
        for spec in specs:
            # Validate the SDK *before* building the engine. Building first
            # means the second import either fails with a bare
            # ModuleNotFoundError, or — worse — succeeds and silently
            # downgrades a shared dependency out from under the engine
            # that is already live. That is exactly how installing tronpy
            # into ubotpay's venv broke its payments.
            sdk = _sdk_for(spec)
            conflict = next((s for s in seen_sdks if sdks_conflict(s, sdk)), None)
            if conflict:
                raise NotSupported(
                    f"profile mixes SDKs {conflict!r} and {sdk!r}; they cannot "
                    f"share an interpreter (tronpy pins eth-abi<6, web3 pins "
                    f"eth-abi>=6). Run them as separate profiles with separate "
                    f"venvs."
                )
            seen_sdks.add(sdk)
            eng = build_engine(spec)
            self.engines[eng.chain_id()] = eng
        if not self.engines:
            raise NotSupported("profile produced no engines")

    # ── the surface the rest of the app uses ──
    @property
    def primary_symbol(self) -> str:
        """The token this deployment settles in by default."""
        try:
            return self.primary.primary_symbol
        except Exception:
            return "USDC"

    @property
    def primary(self) -> ChainEngine:
        """The chain the treasury settles on.

        A property, not a method: chain.py reads `_reg().primary`
        and calls `_reg().primary.stable_symbol` directly. When this
        was a plain method, those calls got the bound function instead
        of the engine, and every payment died with
        "'function' object has no attribute 'submit'".
        """
        return next(iter(self.engines.values()))

    def engine_for(self, chain_id=None) -> ChainEngine:
        if chain_id is None:
            return self.primary
        eng = self.engines.get(chain_id)
        if eng is None:
            raise NotSupported(f"chain {chain_id} is not in this profile")
        return eng

    def chains(self) -> list:
        """The chains this deployment runs, with their tokens.

        The `tokens` list is what the payment modal needs to know what
        it may offer: USolPay accepts USDC and USDT, and a selector
        that only knows about USDC would hide half the product. The
        key is absent for engines that declare a single token, so old
        consumers are unaffected.
        """
        out = []
        for e in self.engines.values():
            entry = {"id": e.chain_id(), "name": e.name,
                     "native": e.native_symbol, "explorer": e.explorer()}
            try:
                toks = e.tokens()
            except AttributeError:
                toks = []
            if len(toks) > 1:
                entry["tokens"] = [
                    {"symbol": t["symbol"], "mint": t["mint"],
                     "decimals": t["decimals"]}
                    for t in toks
                ]
            out.append(entry)
        return out


def load_profile(name_or_path: str) -> dict:
    """Load a profile by bare name ('utronpay') or by path."""
    path = name_or_path
    if not os.path.sep in name_or_path and not name_or_path.endswith((".yaml", ".yml")):
        path = os.path.join(PROFILE_DIR, name_or_path + ".yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no profile at {path}")
    prof = _load_yaml(path)
    prof["_path"] = path
    # Env overrides win over the file so a secret never needs to be committed.
    #
    # The env var names are USOL_* for this deployment. The old U*
    # names are still honoured so a config carried over from another
    # deployment keeps working, but the USOL_* one is checked first:
    # reading only U* would mean USolPay silently ignored its own
    # revenue address and charged fees that went nowhere.
    for key in ("private_key", "revenue_address", "api_key"):
        # The prefixed name wins; the unprefixed one is the legacy
        # fallback so a config carried over from another deployment
        # keeps working.
        env = (os.environ.get("%s_%s" % (PREFIX, key.upper()))
               or os.environ.get("U%s" % key.upper()))
        if env:
            # The `chains:` block may be a single spec (a dict) or a list
            # of them. Iterating a dict yields its KEYS, so `for spec in
            # chains` would hand the loop a string and `isinstance(spec,
            # dict)` would be False -- the env override would then never
            # be written, silently. Normalise to a list of dicts first.
            chains = prof.get("chains")
            if isinstance(chains, dict):
                specs = [chains]
            elif isinstance(chains, list):
                specs = [c for c in chains if isinstance(c, dict)]
            else:
                specs = [prof]
            for spec in specs:
                spec[key] = env
    return prof


def active_profile_name() -> str:
    """Which profile this deployment runs.

    The env var is this deployment's own: USONIC_PROFILE for
    usonicpay, USOL_PROFILE for usolpay. The old Utron_PROFILE name is
    still honoured so a config carried over from the Tron deployment
    keeps working, but the default is this deployment's own profile.

    The default used to be the literal "usolpay" - the Solana
    deployment's profile name - in every deployment. Whenever the
    PROFILE env var was not read, a service looked for a profile that
    did not exist in it, or one that existed and was the wrong chain.
    """
    env_prefix = os.environ.get("UBOT_ENV_PREFIX") or "USONIC"
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
    return env_prefix.lower()