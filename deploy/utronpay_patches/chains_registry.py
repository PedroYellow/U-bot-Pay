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
            chain_id=spec.get("chain_id"),
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
            private_key_hex=spec.get("private_key"),
            revenue_address=spec.get("revenue_address"),
            stable_symbol=spec.get("stable_symbol", "USDC"),
        )
    if kind == "solana":
        # Solana is native: ed25519 keys and base58 addresses, so the
        # engine takes a signer object rather than a private_key_hex.
        # The key never enters this function -- the caller decides
        # whether a signer exists (read-only replica vs funded treasury).
        from .solana import SolanaEngine
        from .solana_signer import FileSigner, NullSigner

        key_path = spec.get("key_path") or spec.get("private_key")
        signer = FileSigner(key_path) if key_path else NullSigner()
        return SolanaEngine(
            profile=spec.get("profile") or {},
            signer=signer,
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
    def primary(self) -> ChainEngine:
        """The chain the treasury settles on."""
        return next(iter(self.engines.values()))

    def engine_for(self, chain_id=None) -> ChainEngine:
        if chain_id is None:
            return self.primary
        eng = self.engines.get(chain_id)
        if eng is None:
            raise NotSupported(f"chain {chain_id} is not in this profile")
        return eng

    def chains(self) -> list:
        return [{"id": e.chain_id(), "name": e.name,
                 "native": e.native_symbol, "explorer": e.explorer()}
                for e in self.engines.values()]


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
    # UtronPay's env names are brand-prefixed and do not always match the
    # generic profile keys. The revenue destination lives in
    # UTRON_TREASURY_ADDRESS, and the env shim exports it as
    # UTRON_TREASURY_ADDRESS and UTREASURY_ADDRESS (and as
    # UREVENUE_ADDRESS when it is set under that name). All of them are
    # checked here, because a single missed alias leaves
    # chain.REVENUE_ADDRESS empty and the fee with nowhere to go.
    env_aliases = {
        "private_key": ("UPRIVATE_KEY", "UTRON_PRIVATE_KEY"),
        "revenue_address": ("UREVENUE_ADDRESS", "UTREASURY_ADDRESS",
                            "UTRON_TREASURY_ADDRESS", "UTRON_REVENUE_ADDRESS"),
        "api_key": ("UAPI_KEY", "UTRON_API_KEY"),
    }
    for key, env_names in env_aliases.items():
        env = next((os.environ.get(name) for name in env_names if os.environ.get(name)), None)
        if env:
            for spec in (prof.get("chains") or [prof]):
                if isinstance(spec, dict):
                    spec[key] = env
    return prof


def active_profile_name() -> str:
    return os.environ.get("UTRON_PROFILE", "utronpay")