"""
Solana chain engine — native implementation for USolPay.

Solana is not EVM. None of the assumptions that hold for
`chains/evm.py` apply here, and importing them is how a payment engine
ends up sending to the wrong place:

  - Keys are ed25519, not secp256k1. `web3.py` is useless.
  - Addresses are base58, no 0x prefix.
  - There are no contracts. An SPL token transfer is an instruction to a
    associated token account (ATA), not a `transfer()` call on a contract.
  - Network fee and ATA rent are paid in **SOL**, not in the product
    token. A treasury holding USDC and zero SOL cannot send anything.
  - `transferWithAuthorization` does not exist, so x402 must fail closed.
    The base class already raises NotSupported; `supports_x402 = False`
    makes every higher-level check refuse before we get there.

The base58 length trap, recorded because it is not fixable:

  A 32-byte pubkey encodes to 43 **or** 44 base58 characters depending on
  its value. Dropping one character from a 44-char address therefore
  yields a different pubkey whose encoding is itself canonical — it
  decodes, it round-trips, and it is a valid 32-byte key. No regex or
  codec can tell that apart from a legitimately 43-char address, because
  the two are the same thing. Rejecting 43-char addresses would reject
  real wallets, so this engine rejects only what is genuinely wrong:

      length > 44 · non-base58 characters · surrounding whitespace ·
      EVM (0x+40 hex) · Tron (T-base58check)

  and the frontend must do the human confirmation for the 43-char case.
  `_SHORT_ADDR_CHARS = 43` exists so the frontend can key off it.

Everything in submit() fails closed. If the ATA cannot be created, if the
treasury has no SOL for fees, if the destination does not validate, or if
no signer is configured, nothing is broadcast.
"""
import asyncio
import json
import os
import threading
import time

from . import solana_tokens as _tokens
from .engine import ChainEngine, NotSupported

# ── lazily imported so a process that never loads the Solana profile
# never pays for the SDK, and never risks it colliding with another one.
_sol = None
_solders = None
_import_lock = threading.Lock()


def _load_sdk():
    """Import solana-py + solders on first use.

    The registry's conflict check runs against `sdk`, so by the time we
    are called it has already established that nothing else in this
    interpreter wants web3 or tronpy.
    """
    global _sol, _solders
    if _sol is not None:
        return
    with _import_lock:
        if _sol is not None:
            return
        import solders.message as sm
        import solders.pubkey as sp
        import solders.signature as ss
        import solders.system_program as ssys
        import solders.transaction as stx
        from solana.rpc.async_api import AsyncClient, TokenAccountOptsModel
        from solana.rpc.commitment import Confirmed, Processed

        _sol = {
            "AsyncClient": AsyncClient,
            # Enum instances, not classes. See balance()'s comment.
            "Confirmed": Confirmed,
            "Processed": Processed,
            "TokenAccountOptsModel": TokenAccountOptsModel,
        }
        _solders = {
            "Pubkey": sp.Pubkey,
            "Signature": ss.Signature,
            # Message is for the transaction the USER signs: a message
            # without a signature, which the wallet completes.
            "Message": sm.Message,
            "system_program": ssys,
            "transaction": stx,
        }


# SPL Token program. Holds the token accounts and does the transfers.
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

# Associated Token Account program. A SEPARATE program from the token
# program, and confusing them is not a detail: the ATA ADDRESS is
# derived with this program as the PDA program, and the account-creation
# instruction is issued to this program, not the token one.
#
# Getting this wrong produces a valid-looking base58 address that is
# simply not the account -- the payment would go nowhere and nothing
# would say so. Verified on-chain against a wallet's real ATA.
ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"

# The System program. Creates accounts and moves SOL.
SYSTEM_PROGRAM = "11111111111111111111111111111111"

# The same address as a Pubkey, for the RPC filter. Computed lazily
# because the SDK import happens on first use.
_SPL_TOKEN_PROGRAM_PUBKEY = None


def _spl_program_pubkey():
    global _SPL_TOKEN_PROGRAM_PUBKEY
    if _SPL_TOKEN_PROGRAM_PUBKEY is None:
        _SPL_TOKEN_PROGRAM_PUBKEY = _solders["Pubkey"].from_string(
            SPL_TOKEN_PROGRAM)
    return _SPL_TOKEN_PROGRAM_PUBKEY

# A 32-byte pubkey is 43 or 44 base58 chars; see the module docstring.
_SHORT_ADDR_CHARS = 43
_ADDR_CHARS_MAX = 44


def _is_stale_blockhash(exc: Exception) -> bool:
    """Did the chain reject this because the blockhash had expired?

    The public endpoint's hash can go stale between building the
    transaction and submitting it. The error says so plainly:
    BlockhashNotFound.
    """
    msg = str(exc).lower()
    return "blockhashnotfound" in msg or "block hash not found" in msg


def _is_transport(exc: Exception) -> bool:
    """Is this a transport-level failure (DNS, TCP, timeout)?

    Those are worth retrying on a different endpoint: the endpoint is
    unreachable, not the request being wrong.
    """
    import httpx
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout,
                        httpx.ReadTimeout, httpx.WriteTimeout,
                        httpx.PoolTimeout, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, OSError):
        return True
    msg = str(exc).lower()
    return ("connection" in msg or "timed out" in msg
            or "temporary failure in name resolution" in msg
            or "nodename nor servname" in msg
            or "network is unreachable" in msg)


def _is_rate_limit(exc: Exception) -> bool:
    """Is this exception the RPC rate-limiting us?

    The SDK wraps the HTTP status in a SolanaRpcException whose text
    mentions the status code, and httpx raises HTTPStatusError with
    `response.status_code`. Both shapes have to be recognised: the
    solana-py version differs between releases.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "too many requests" in text


class SolanaEngine(ChainEngine):
    """USolPay: SPL USDC on Solana mainnet."""

    name = "usolpay"
    native_symbol = "SOL"
    stable_symbol = "USDC"
    sdk = "solana"
    supports_x402 = False
    evm = False

    # Filled in from the profile by the registry.
    stable_address = ""          # SPL mint, base58
    _chain_id = 1151111081099710  # LI.FI internal id, bridge compatibility only
    _explorer = "https://solscan.io"

    _decimals = 6
    _revenue = ""
    _ata_policy = "create"
    _min_sol_reserve = 0.01
    _confirm_interval = 1.5
    _confirm_timeout = 60
    _rpc_url = "https://api.mainnet-beta.solana.com"
    # How many times to retry a rate-limited RPC call, and the base
    # backoff in seconds. The public endpoint answers 429 under load;
    # a paid endpoint (Helius, QuickNode) raises these limits, but the
    # engine must not fall over when the limit is hit either way.
    _rpc_retries = 4
    _rpc_backoff = 0.5

    def __init__(self, profile: dict | None = None, signer=None,
                 spec: dict | None = None):
        """`profile` is the whole profile document; `spec` is its
        `chains:` block, which is where the registry writes the
        environment-provided values (revenue_address, private_key).
        They are different levels of the same file and the spec is the
        one that carries the live configuration."""
        _load_sdk()
        prof = profile or {}
        spec = spec or {}
        spec_revenue = spec.get("revenue_address") or ""

        # The token catalogue. USolPay accepts USDC and USDT, each with
        # its own mint, so "the token" is now a set of them and a
        # payment names which one it is in.
        self._tokens = _tokens.token_catalogue(prof)
        if not self._tokens:
            raise ValueError("profile declares no tokens")
        primary = _tokens.resolve_token(prof)
        self._primary_symbol = primary["symbol"]
        self.stable_address = primary["mint"]
        self._decimals = primary["decimals"]
        if prof.get("name"):
            self.name = prof["name"]

        chain = prof.get("chain") or {}
        if chain.get("lifi_id"):
            self._chain_id = int(chain["lifi_id"])
        if chain.get("native_symbol"):
            self.native_symbol = chain["native_symbol"]

        rpc = prof.get("rpc") or {}
        if rpc.get("url"):
            self._rpc_url = rpc["url"]
        # The pool of endpoints to try. A profile may list several;
        # the first is the primary and the rest are fallbacks.
        urls = rpc.get("urls") or []
        if isinstance(urls, str):
            urls = [urls]
        self._rpc_urls = [u for u in urls if u]
        if self._rpc_url not in self._rpc_urls:
            self._rpc_urls.insert(0, self._rpc_url)

        # A paid endpoint from the environment wins: SOLANA_RPC_URLS
        # holds a comma-separated list, tried in order. The public
        # endpoint stays in the pool as the last resort, so the service
        # keeps serving even if the key expires or the provider has an
        # outage. This is how the deployment scales without a code
        # change.
        env_urls = os.environ.get("SOLANA_RPC_URLS", "").strip()
        if env_urls:
            extra = [u.strip() for u in env_urls.split(",") if u.strip()]
            merged = []
            for u in extra + self._rpc_urls:
                if u and u not in merged:
                    merged.append(u)
            self._rpc_urls = merged
            self._rpc_url = merged[0]
        if rpc.get("confirm_interval"):
            self._confirm_interval = float(rpc["confirm_interval"])
        if rpc.get("confirm_timeout"):
            self._confirm_timeout = int(rpc["confirm_timeout"])

        if "ata_policy" in prof:
            self._ata_policy = prof["ata_policy"]
        if prof.get("min_sol_reserve") is not None:
            self._min_sol_reserve = float(prof["min_sol_reserve"])
        # The revenue address comes from the environment, which the
        # registry writes into the *spec* (profile["chains"]). The
        # profile's own revenue_address key is a fallback for a
        # deployment that hardcodes it. The spec wins, because the
        # env is where a real address belongs and a stale empty string
        # in the file must not shadow it.
        if spec_revenue:
            self._revenue = spec_revenue
        elif prof.get("revenue_address"):
            self._revenue = prof["revenue_address"]

        exp = prof.get("explorer") or {}
        if exp.get("base"):
            self._explorer = exp["base"].rstrip("/")

        # A signer is only present in a process that actually holds the
        # treasury key. Reads never need one, so the engine is usable in
        # a read-only replica with signer=None.
        self._signer = signer
        self._client = None
        self._client_loop = None
        self._client_url = None
        self._balance_cache = {}
        self._lock = threading.RLock()
        self._treasury = None

    # ── units ────────────────────────────────────────────────────
    def decimals(self, symbol: str | None = None) -> int:
        """Decimals of a token. Both USDC and USDT are 6, but the
        lookup is per token so a future 9-decimal token does not
        silently inherit 6."""
        if symbol is None:
            return self._decimals
        return self.token(symbol)["decimals"]

    def tokens(self) -> list:
        """Every token this deployment accepts, as a list of dicts with
        symbol, mint and decimals."""
        return [dict(t) for t in self._tokens.values()]

    def token(self, symbol: str | None = None) -> dict:
        """Resolve a token by symbol. Raises ValueError naming the
        accepted symbols if the symbol is unknown."""
        sym = _tokens.normalise_symbol(symbol) or self._primary_symbol
        if sym not in self._tokens:
            raise ValueError(
                f"unknown token {sym!r}; this deployment accepts: "
                + ", ".join(sorted(self._tokens))
            )
        return dict(self._tokens[sym])

    def to_units(self, amount, symbol: str | None = None) -> int:
        """Human amount -> base units. Same integer-decimals approach as
        the EVM engine: no float ever touches a value of money."""
        return _tokens.to_units(amount, self.decimals(symbol))

    def from_units(self, units: int, symbol: str | None = None):
        return _tokens.from_units(units, self.decimals(symbol))

    # ── validation ───────────────────────────────────────────────
    def is_address(self, addr: str) -> bool:
        """Base58, 43-44 chars, canonical round-trip.

        Deliberately NOT trimming whitespace: a pasted address with a
        trailing space is a paste error, and silently repairing it is how
        a payment ends up somewhere the operator did not intend.
        """
        if not isinstance(addr, str) or not addr:
            return False
        if len(addr) > _ADDR_CHARS_MAX or len(addr) < _SHORT_ADDR_CHARS:
            return False
        try:
            pk = _solders["Pubkey"].from_string(addr)
        except Exception:
            return False
        return str(pk) == addr

    def is_tx_hash(self, value: str) -> bool:
        """A Solana signature is 64 bytes -> 86..88 base58 chars."""
        if not isinstance(value, str) or not (86 <= len(value) <= 88):
            return False
        try:
            sig = _solders["Signature"].from_string(value)
        except Exception:
            return False
        return str(sig) == value

    @property
    def primary_symbol(self) -> str:
        """The token this deployment settles in by default."""
        return self._primary_symbol

    @property
    def rpc(self) -> str:
        """The primary RPC endpoint.

        api.py checks `target.rpc` to decide whether a chain can be
        read from -- a destination-only chain has none. The Solana
        engine has one, so it must expose the attribute in the same
        shape the other engines do, or the endpoint raises
        AttributeError instead of answering.
        """
        return self._rpc_url

    def explorer_url(self, tx: str | None) -> str | None:
        if not tx:
            return None
        return f"{self._explorer}/tx/{tx}"

    # ── client ───────────────────────────────────────────────────
    async def _get_client(self, url: str | None = None):
        """An AsyncClient bound to the current event loop.

        The AsyncClient's httpx transport binds to the event loop that
        was running when it was created. Because _run() starts and ends
        a fresh loop per call, a cached client from a previous call is
        already dead -- the second read raises "Event loop is closed".
        So the cache is keyed on the running loop, not global.

        `url` selects a specific endpoint. The failover pool passes the
        one it wants to try.
        """
        loop = asyncio.get_event_loop()
        target = url or self._rpc_url
        if self._client is not None and self._client_loop is loop \
                and self._client_url == target:
            return self._client
        if self._client is not None:
            # Stale client bound to a finished loop, or a different
            # endpoint than the one cached. Drop it rather than reuse it;
            # the new one is created on this loop below.
            self._client = None
        self._client = _sol["AsyncClient"](target)
        self._client_loop = loop
        self._client_url = target
        return self._client

    def _rpc_pool(self) -> list[str]:
        """The RPC endpoints to try, best first.

        A paid endpoint (Helius, QuickNode, ...) goes first when a key
        is configured -- it is the one with the guarantees. The public
        endpoint is always the fallback, so the service still works if
        the key expires or the provider has an outage.

        The public endpoint is not free of limits: it rate-limits by IP
        and answers 429 under modest load. Failing over between two
        endpoints halves the pressure, and the balance cache absorbs
        most of the repeated reads.
        """
        pool = []
        for url in self._rpc_urls:
            if url and url not in pool:
                pool.append(url)
        if not pool:
            pool = [self._rpc_url]
        return pool

    async def _call_pool(self, fn_name: str, *args, **kwargs):
        """Call an RPC method, failing over between endpoints.

        `fn_name` is the AsyncClient method to call; `fn` in `_rpc` is
        the bound method. On a 429 or a transport error, the next
        endpoint in the pool is tried. If every endpoint fails, the
        last error is raised.
        """
        pool = self._rpc_pool()
        last = None
        for idx, url in enumerate(pool):
            try:
                client = await self._get_client(url)
                fn = getattr(client, fn_name)
                return await self._rpc(fn, *args, **kwargs)
            except Exception as e:
                last = e
                if not _is_rate_limit(e) and not _is_transport(e):
                    raise
                if idx + 1 < len(pool):
                    # Move to the next endpoint. The current client is
                    # dropped so the next call binds a fresh one.
                    self._client = None
                    self._client_loop = None
                    continue
        raise last

    async def _rpc(self, fn, *args, **kwargs):
        """Call an RPC method, retrying on 429.

        The public Solana endpoint rate-limits by IP and answers 429
        (Too Many Requests) under even modest load -- and /balance is
        the most-called endpoint in the product. A 429 is transient and
        the right response is to back off and try again, not to fail the
        request.

        Retries with exponential backoff and jitter: without the jitter,
        every concurrent caller retries at the same instant and the
        limit is hit again immediately.
        """
        import random

        last = None
        for attempt in range(self._rpc_retries + 1):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                last = e
                if not _is_rate_limit(e):
                    raise
                if attempt >= self._rpc_retries:
                    break
                delay = min(self._rpc_backoff * (2 ** attempt), 8.0)
                delay *= 0.5 + random.random()   # jitter
                await asyncio.sleep(delay)
        raise last

    async def _aclose(self):
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None
            self._client_loop = None

    def _run(self, coro):
        """Run a coroutine from sync code.

        The rest of the codebase is sync; the Solana SDK is async. A
        fresh loop per call is safe here because no call outlives its
        `await` and the AsyncClient is not shared across threads except
        under `self._lock`.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # Called from inside a running loop: run in a worker thread so we
        # neither block the loop nor raise.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(lambda: asyncio.run(coro)).result()

    # ── reads ────────────────────────────────────────────────────
    # ── balance cache ────────────────────────────────────────────
    # /balance is the most-called endpoint in the product, and the
    # public Solana RPC rate-limits by IP. A short-lived cache turns a
    # burst of duplicate reads into one RPC call.
    #
    # The TTL is deliberately short. A balance is money: a stale
    # number that stays wrong for minutes is worse than a slow
    # answer. 12 seconds is enough to absorb a burst of duplicate
    # reads and short enough that a payment is reflected almost
    # immediately.
    _BALANCE_TTL = 12.0
    _BALANCE_CACHE_MAX = 512

    def _cache_balance(self, key: str):
        import time as _time
        ent = self._balance_cache.get(key)
        if ent is None:
            return None
        ts, val = ent
        if _time.time() - ts > self._BALANCE_TTL:
            self._balance_cache.pop(key, None)
            return None
        return val

    def _store_balance(self, key: str, val) -> None:
        import time as _time
        if len(self._balance_cache) >= self._BALANCE_CACHE_MAX:
            # Drop the oldest entry. Simple and bounded: the cache is
            # a burst absorber, not a store.
            try:
                oldest = min(self._balance_cache.items(),
                             key=lambda kv: kv[1][0])
                self._balance_cache.pop(oldest[0], None)
            except (ValueError, TypeError):
                self._balance_cache.clear()
        self._balance_cache[key] = (_time.time(), val)

    def balance(self, address: str, symbol: str | None = None) -> dict:
        """Balances of `address`.

        With no symbol: every configured token, under `tokens`, keyed by
        symbol. With a symbol: that one token's detail.

        Reports `settled` and `pending` separately because on Solana a
        confirmed transfer is final and an unconfirmed one may still
        disappear. Anything that spends this balance has to know which
        it is looking at.

        The flat `settled`/`pending`/`units` keys at the top level are
        the PRIMARY token's (USDC), so callers that only care about
        one asset keep working.
        """
        if not self.is_address(address):
            raise ValueError(f"invalid solana address: {address!r}")

        wanted = ([self.token(symbol)] if symbol
                  else [self.token(s) for s in self._tokens])
        owner = _solders["Pubkey"].from_string(address)

        # A short-lived cache. /balance is the most-called endpoint in
        # the product and the public RPC rate-limits by IP; the cache
        # turns a burst of duplicate reads into one RPC call.
        cache_key = f"{address}|{symbol or 'ALL'}"
        cached = self._cache_balance(cache_key)
        if cached is not None:
            return cached

        async def go():
            c = await self._get_client()
            # ONE call per commitment for ALL tokens, not one per token
            # per commitment. With two tokens the naive version makes
            # four RPC calls, and the public Solana endpoint answers 429
            # (SolanaRpcException) well before that -- the balance
            # endpoint is the most-called one in the product.
            #
            # Asking for every token account of the owner and filtering
            # by mint locally is one call and gives the same numbers.
            # The payload is larger when the owner holds many tokens,
            # which is the trade: fewer round trips, more bytes.
            wanted_mints = {t["mint"] for t in wanted}

            def by_mint(resp):
                out = {}
                for acc in (resp.value or []):
                    info = acc.account.data.parsed["info"]
                    mint = info["mint"]
                    if mint not in wanted_mints:
                        continue
                    amt = int(info["tokenAmount"]["amount"])
                    out[mint] = out.get(mint, 0) + amt
                return out

            # Confirmed / Processed are enum *instances* in solana
            # 0.41.x, not classes -- calling them raises.
            committed = await self._call_pool(
                "get_token_accounts_by_owner_json_parsed",
                owner=owner,
                opts=_sol["TokenAccountOptsModel"](program_id=_spl_program_pubkey()),
                commitment=_sol["Confirmed"],
            )
            pending = await self._call_pool(
                "get_token_accounts_by_owner_json_parsed",
                owner=owner,
                opts=_sol["TokenAccountOptsModel"](program_id=_spl_program_pubkey()),
                commitment=_sol["Processed"],
            )

            settled_by = by_mint(committed)
            pending_by = by_mint(pending)

            out = {}
            for tok in wanted:
                u = settled_by.get(tok["mint"], 0)
                p = pending_by.get(tok["mint"], 0)
                out[tok["symbol"]] = {
                    "mint": tok["mint"],
                    "decimals": tok["decimals"],
                    "units": u,
                    "settled": _tokens.from_units(u, tok["decimals"]),
                    "pending_units": p,
                    "pending": _tokens.from_units(p, tok["decimals"]),
                }

            # Also report SOL, because a token balance is useless if the
            # wallet cannot pay the fee to move it.
            lam = (await self._rpc(c.get_balance, owner)).value
            return out, lam

        per_token, lamports = self._run(go())

        primary = per_token.get(self._primary_symbol, {})
        result = {
            "address": address,
            "chain": self.name,
            "symbol": self._primary_symbol,
            "decimals": self._decimals,
            "units": primary.get("units", 0),
            "settled": primary.get("settled", 0),
            "pending_units": primary.get("pending_units", 0),
            "pending": primary.get("pending", 0),
            # Every token, keyed by symbol.
            "tokens": per_token,
            # Native balance, because spendability depends on it.
            "native_symbol": self.native_symbol,
            "native_lamports": lamports,
            "native_balance": lamports / 10 ** 9,
        }
        if symbol is not None:
            tok = per_token.get(_tokens.normalise_symbol(symbol))
            if tok:
                result.update(tok)
                result["symbol"] = _tokens.normalise_symbol(symbol)
        self._store_balance(cache_key, result)
        return result

    def sol_balance(self, address: str) -> float:
        """SOL available for fees. Send paths check this before doing
        anything, because a wallet with USDC and no SOL is not payable."""
        if not self.is_address(address):
            raise ValueError(f"invalid solana address: {address!r}")

        async def go():
            c = await self._get_client()
            r = await self._rpc(c.get_balance,
                                _solders["Pubkey"].from_string(address))
            return r.value

        return self._run(go()) / 10 ** 9

    def ata_address(self, owner: str, mint: str | None = None) -> str:
        """Associated Token Account for `owner`.

        Derived, not looked up: the ATA address is a deterministic
        function of (owner, mint, token program), so we can compute the
        account a transfer will need without asking the chain first.

        `mint` may be a mint ADDRESS or a token SYMBOL. That matters
        with more than one token: a recipient's USDC ATA and their USDT
        ATA are different accounts, and a transfer to the wrong one
        would move a different asset than intended.
        """
        if not self.is_address(owner):
            raise ValueError(f"invalid solana address: {owner!r}")
        if mint is None:
            mint = self.stable_address
        else:
            # A symbol resolves to its mint; an address passes through.
            sym = _tokens.normalise_symbol(mint)
            if sym in self._tokens:
                mint = self._tokens[sym]["mint"]
        mint_pk = _solders["Pubkey"].from_string(mint)
        owner_pk = _solders["Pubkey"].from_string(owner)
        # The SEED uses the token program; the PDA PROGRAM is the ATA
        # program. Using the token program for both produces a
        # different, non-existent address.
        token_prog = _solders["Pubkey"].from_string(SPL_TOKEN_PROGRAM)
        ata_prog = _solders["Pubkey"].from_string(ATA_PROGRAM)
        ata, _ = _solders["Pubkey"].find_program_address(
            [bytes(owner_pk), bytes(token_prog), bytes(mint_pk)],
            ata_prog,
        )
        return str(ata)

    def has_ata(self, owner: str, mint: str | None = None) -> bool:
        """Does the recipient's ATA already exist?

        Determines whether a transfer can be a single instruction or
        needs an ATA-creation instruction first. `mint` may be a symbol
        or a mint address.
        """
        try:
            ata = self.ata_address(owner, mint)
        except ValueError:
            return False

        async def go():
            c = await self._get_client()
            r = await c.get_account_info(_solders["Pubkey"].from_string(ata))
            return r.value is not None

        return bool(self._run(go()))

    # ── money movement ───────────────────────────────────────────
    def submit(self, to: str, amount, fee: float = 0.0,
               symbol: str | None = None) -> dict:
        """Build and broadcast the payment, plus a second TX for the
        product fee. Mirrors the Tron engine's two-transaction shape.

        The payment is in `symbol` (default: the primary token). The
        fee is charged and paid out in the SAME token -- one asset for
        the whole operation, so the user pays one fee and the treasury
        only needs to hold what it is sending.

        Fails closed, in this order, before anything is broadcast:

          1. destination must validate (base58, canonical)
          2. the token must be one this deployment accepts
          3. amount must be a positive integer number of base units
          4. a signer must exist — no signer, no payment
          5. the treasury must hold SOL for the fee and any ATA rent

        Only after all five does it build a transaction.
        """
        if not self.is_address(to):
            raise ValueError(f"invalid solana address: {to!r}")

        tok = self.token(symbol)
        sym = tok["symbol"]

        amount_u = self.to_units(amount, sym)
        if amount_u <= 0:
            raise ValueError(f"amount must be positive, got {amount!r}")

        if self._signer is None:
            raise NotSupported(
                "no Solana signer configured; this process cannot send. "
                "Set the treasury key in the service environment."
            )

        treasury = self._treasury_address()
        if treasury is None:
            raise NotSupported(
                "this process has no Solana treasury key, so it can read "
                "balances but cannot send. Point USOLPAY_TREASURY_KEY at the "
                "key file (or create /opt/usolpay/secrets/treasury.key) and "
                "restart."
            )

        fee_u = self.to_units(fee, sym) if fee and fee >= 0.01 else 0

        # Both transfers need SOL for rent and priority fees. Refuse
        # rather than discover the shortfall on-chain after signing.
        need_sol = self._estimate_sol_need(to, fee_u > 0, sym)
        have_sol = self.sol_balance(treasury)
        if have_sol < self._min_sol_reserve:
            raise RuntimeError(
                f"treasury {treasury} holds {have_sol:.6f} SOL, below the "
                f"{self._min_sol_reserve} reserve; cannot pay network fees"
            )
        if have_sol - need_sol < 0.001:
            raise RuntimeError(
                f"treasury {treasury} holds {have_sol:.6f} SOL; payment needs "
                f"about {need_sol:.6f} SOL for rent and fees. Fund it."
            )

        with self._lock:
            tx = self._run(self._send(to, amount_u, sym))
            fee_tx = None
            if fee_u and self._revenue and self.is_address(self._revenue):
                fee_tx = self._run(self._send(self._revenue, fee_u, sym))

        return {
            "tx": tx,
            "fee_tx": fee_tx,
            "fee_usdc": fee if fee_tx else 0.0,
            "symbol": sym,
            "mint": tok["mint"],
            "explorer": self.explorer_url(tx),
            "fee_explorer": self.explorer_url(fee_tx),
        }

    def confirm(self, tx: str, fee_tx: str | None = None, timeout: int = 60) -> dict:
        """Poll until the signature is final.

        Solana's confirmation is a commitment level, not a receipt with a
        success field: once a signature is `final` it cannot be undone
        or double-spent. So "did it succeed" is answered by the
        commitment, and a signature that never appears is treated as a
        failure rather than left pending forever.
        """
        if not self.is_tx_hash(tx):
            raise ValueError(f"invalid solana signature: {tx!r}")

        t0 = time.time()
        deadline = t0 + (timeout or self._confirm_timeout)
        base = {
            "tx": tx,
            "ok": False,
            "slot": 0,
            "explorer": self.explorer_url(tx),
            "fee_tx": fee_tx,
            "fee_explorer": self.explorer_url(fee_tx),
            "latency_s": 0.0,
        }

        while time.time() < deadline:
            try:
                st = self._run(self._status(tx))
            except Exception:
                # A transient RPC failure is not a failed payment. Keep
                # polling: the signature may well have landed.
                st = None
            if st:
                base["slot"] = st.get("slot", 0)
                base["confirmations"] = st.get("confirmations")
                if st.get("err") is not None:
                    base["error"] = st["err"]
                    base["latency_s"] = round(time.time() - t0, 2)
                    return base
                if st.get("commitment") in ("finalized", "final", "confirmed"):
                    base["ok"] = True
                    base["latency_s"] = round(time.time() - t0, 2)
                    return base
            time.sleep(self._confirm_interval)

        base["error"] = "timeout waiting for confirmation"
        base["latency_s"] = round(time.time() - t0, 2)
        return base

    def send(self, to: str, amount, memo_hash: str | None = None,
             fee: float = 0.0, symbol: str | None = None) -> dict:
        """submit + confirm. A confirmed-but-failed payment is an error,
        not a success with a caveat."""
        res = self.send_with_fee(to, amount, fee=fee, symbol=symbol)
        if not res.get("ok"):
            raise RuntimeError(
                f"solana transfer not confirmed: {res.get('error') or 'unknown'} "
                f"(tx {res.get('tx')})"
            )
        return res

    def send_with_fee(self, to: str, amount, fee: float = 0.0,
                       timeout: int = 60, symbol: str | None = None) -> dict:
        sub = self.submit(to, amount, fee, symbol=symbol)
        res = self.confirm(sub["tx"], sub.get("fee_tx"), timeout)
        res["symbol"] = sub.get("symbol")
        res["mint"] = sub.get("mint")
        return res

    # ── x402 ─────────────────────────────────────────────────────
    # Inherited: supports_x402 is False, so the base class raises
    # NotSupported. Stated here so a reader does not assume it was
    # forgotten. transferWithAuthorization is ERC-20; SPL has no
    # equivalent, so there is nothing to implement.

    # ── internals ────────────────────────────────────────────────
    def has_signer(self) -> bool:
        """Can this process sign?

        False in a read-only deployment: no key, or a NullSigner. The
        startup check asks this rather than looking for an env var,
        because on Solana the key lives in a file, not the environment.
        """
        signer = self._signer
        if signer is None:
            return False
        if type(signer).__name__ == "NullSigner":
            return False
        try:
            return signer.pubkey() is not None
        except Exception:
            return False

    def _treasury_address(self) -> str | None:
        if self._treasury:
            return self._treasury
        addr = self._signer.pubkey() if self._signer else None
        self._treasury = str(addr) if addr else None
        return self._treasury

    def _estimate_sol_need(self, to: str, has_fee_tx: bool,
                           symbol: str | None = None) -> float:
        """Rough SOL cost: ATA rent if the recipient has none, plus a
        small priority fee per signature. Deliberately generous — the
        check is there to stop a payment that cannot possibly land, not
        to predict the exact price.

        The ATA is per (recipient, token): a recipient who has never
        held USDT needs a USDT ATA even if they already hold USDC."""
        sol = 0.000005                      # ~5000 lamports per signature
        if self._ata_policy == "create" and not self.has_ata(to, symbol):
            sol += 0.00205                  # token account rent exemption
        if has_fee_tx:
            sol += 0.000005
        return sol

    async def _fresh_blockhash(self):
        """A blockhash that the chain will accept right now.

        Two things are known about the public endpoint:

        * `getLatestBlockhash` answers with the DEFAULT commitment.
          Asking for `finalized` returns a different, older hash --
          and the older one is the one that gets rejected with
          BlockhashNotFound. So: default commitment, always.
        * The blockhash is single-use for a short window. If the
          transaction is rejected because the hash expired, the fix
          is a NEW hash and a new signature, not a retry of the same
          transaction. `_send` does that.

        So this is deliberately the plain call, and the retry lives in
        the caller.
        """
        c = await self._get_client()
        return (await c.get_latest_blockhash()).value.blockhash

    async def _status(self, sig: str) -> dict | None:
        """The on-chain status of a signature, or None if unknown.

        `get_signature_statuses` needs a Signature object, not a
        string: passing a string raises TypeError, which the caller
        swallowed as "not confirmed yet". So every payment was reported
        as unconfirmed and timed out even though it had landed.
        """
        c = await self._get_client()
        try:
            signature = _solders["Signature"].from_string(sig)
        except Exception:
            return None

        # getSignatureStatuses against the public endpoint answers
        # `null` for a signature that IS on chain -- the node only
        # keeps recent statuses in memory and the signature was not in
        # that window. Every payment was therefore reported as
        # unconfirmed and timed out, even though it had landed.
        #
        # getTransaction asks for the transaction itself, which the
        # node can serve from its ledger or its history. That is the
        # reliable answer, and its `meta.err` is the actual success
        # flag.
        try:
            r = await c.get_transaction(signature, max_supported_transaction_version=0)
        except Exception:
            r = None
        tx = getattr(r, "value", None) if r is not None else None
        if tx is not None:
            meta = getattr(tx, "meta", None)
            err = getattr(meta, "err", None) if meta is not None else None
            return {
                "slot": getattr(tx, "slot", 0),
                "confirmations": None,
                "err": err,
                "commitment": "confirmed" if err is None else "failed",
                "found": True,
            }

        # Not in the ledger yet. Fall back to the status endpoint for
        # the error case, where the node does know the signature.
        try:
            st = await c.get_signature_statuses([signature])
            arr = st.value or []
            if arr and arr[0] is not None:
                s = arr[0]
                return {
                    "slot": s.slot,
                    "confirmations": s.confirmations,
                    "err": s.err,
                    "commitment": getattr(s, "confirmation_status", None) or None,
                    "found": True,
                }
        except Exception:
            pass
        return None

    def tx_status(self, sig: str) -> dict:
        """The on-chain status of a signature.

        Three answers, and the difference between them matters to the
        user:

          confirmed  the transaction is on chain and did not revert
          failed     the transaction is on chain and it reverted
          pending    the chain does not know it yet

        "pending" is NOT a failure. A transaction the chain has not
        seen has not been rejected, and telling the user it failed
        would be wrong.

        The lookup goes through getTransaction, not
        getSignatureStatuses: the public endpoint answers `null` for
        a signature that IS on chain (it only keeps recent statuses
        in memory), which would make every settled payment read as
        pending forever.
        """
        import asyncio as _asyncio

        async def go():
            c = await self._get_client()
            try:
                signature = _solders["Signature"].from_string(sig)
            except Exception:
                return {"status": "pending", "error": None, "slot": None}
            try:
                r = await c.get_transaction(
                    signature, max_supported_transaction_version=0)
            except Exception:
                r = None
            tx = getattr(r, "value", None) if r is not None else None
            if tx is None:
                # getTransaction answers null for a transaction the node
                # has aged out of its cache. getSignatureStatuses with
                # searchTransactionHistory looks through the history
                # instead, and finds it.
                #
                # The two fail in opposite directions: getTransaction
                # drops OLD transactions and getSignatureStatuses
                # answers null for RECENT ones it has not finalised.
                # So a settled payment from a while ago read as pending
                # forever.
                try:
                    # The history scan is slow, and the default client
                    # timeout is not enough for it - the call raised a
                    # ReadTimeout and the fallback answered "pending",
                    # which is the bug it was meant to fix. So this
                    # call gets its own client with a longer deadline.
                    #
                    # It is a separate client on purpose: the cached
                    # one belongs to the current event loop and to the
                    # failover pool's endpoint choice, and this wants
                    # the primary endpoint with more time.
                    _slow = _sol["AsyncClient"](self._rpc_url, timeout=45)
                    try:
                        st = await _slow.get_signature_statuses(
                            [signature], search_transaction_history=True)
                    finally:
                        try:
                            await _slow.close()
                        except Exception:
                            pass
                    entries = getattr(st, "value", None) or []
                    if entries:
                        e = entries[0]
                        conf = getattr(e, "confirmation_status", None)
                        err = getattr(e, "err", None)
                        if conf is not None:
                            return {
                                "status": "failed" if err is not None
                                          else "confirmed",
                                "error": str(err) if err is not None else None,
                                "slot": getattr(e, "slot", None),
                            }
                except Exception:
                    pass
                return {"status": "pending", "error": None, "slot": None}
            meta = getattr(tx, "meta", None)
            err = getattr(meta, "err", None) if meta is not None else None
            return {
                "status": "failed" if err is not None else "confirmed",
                "error": str(err) if err is not None else None,
                "slot": getattr(tx, "slot", None),
            }

        return self._run(go())

    async def build_unsigned(self, from_address: str, to: str,
                             amount_u: int,
                             symbol: str | None = None) -> dict:
        """Build a transfer the USER signs in their own wallet.

        This is the web flow: the browser asks for the transaction, the
        wallet signs it, the browser sends it. The backend never holds
        the user's key and never broadcasts on their behalf.

        Returns the raw transaction bytes (base64) and the addresses
        it touches, so the caller can show the user what they are
        about to sign.

        The layout matches `_send` exactly -- same instruction, same
        account derivation -- because a transaction that differs from
        the one the backend would have built is a transaction nobody
        has reviewed.
        """
        import base64
        import solders.compute_budget as cb
        import solders.transaction as tx_mod

        from . import spl_instructions as spl

        if not self.is_address(from_address):
            raise ValueError(f"invalid solana address: {from_address!r}")
        if not self.is_address(to):
            raise ValueError(f"invalid solana address: {to!r}")

        c = await self._get_client()
        tok = self.token(symbol)
        sender = _solders["Pubkey"].from_string(from_address)
        dest = _solders["Pubkey"].from_string(to)
        mint = _solders["Pubkey"].from_string(tok["mint"])
        program = _solders["Pubkey"].from_string(SPL_TOKEN_PROGRAM)

        # The same derivation as _send: one place, so the two can
        # never disagree about which account is the source.
        src_ata = _solders["Pubkey"].from_string(
            self.ata_address(str(sender), tok["mint"])
        )
        dst_ata = _solders["Pubkey"].from_string(
            self.ata_address(str(dest), tok["mint"])
        )

        # The recipient's ATA may not exist yet. A transfer to a
        # missing account fails on chain, and the user's wallet would
        # show an opaque error. Say so plainly instead.
        info = await c.get_account_info(dst_ata)
        if info.value is None:
            raise ValueError(
                "the recipient has no %s token account yet; it is created "
                "by the first payment to them" % tok["symbol"]
            )

        blockhash = await self._fresh_blockhash()
        # new_signed_with_payer is for the treasury's own key. For a
        # transaction the USER signs, the transaction is built
        # unsigned and the wallet completes it.
        #
        # new_unsigned + populate(blockhash) is the supported path.
        # Building a Message by hand and serializing it does not
        # work: solders' Message has no serialize(), and the
        # hand-rolled bytes came back with zero accounts and zero
        # instructions -- a transaction the runtime would reject.
        # The supported path for a transaction somebody else signs:
        #
        #   Message.new_with_blockhash(instrs, payer, blockhash)
        #   Transaction.new_unsigned(message)
        #   bytes(transaction)
        #
        # new_unsigned takes a MESSAGE, not a list of instructions, and
        # it fills in one empty signature slot per required signer --
        # which is what makes the transaction unsigned rather than
        # broken. The wallet writes its signature into its own slot.
        msg = _solders["Message"].new_with_blockhash(
            [
                cb.set_compute_unit_limit(200_000),
                spl.transfer_checked(
                    source=src_ata,
                    mint=mint,
                    destination=dst_ata,
                    owner=sender,
                    amount=amount_u,
                    decimals=tok["decimals"],
                    program_id=program,
                ),
            ],
            payer=sender,
            blockhash=blockhash,
        )
        tx = _solders["transaction"].Transaction.new_unsigned(msg)
        # A VersionedTransaction, because that is what the browser's
        # wallet and the RPC both speak. The RPC's simulate_transaction
        # rejects a legacy Transaction outright ("'Transaction' object
        # is not an instance of 'VersionedTransaction'"), and the
        # wallets expect the versioned form.
        raw = bytes(_solders["transaction"].VersionedTransaction.from_legacy(tx))
        return {
            "transaction": base64.b64encode(raw).decode(),
            "from_address": from_address,
            "to_address": to,
            "source_account": str(src_ata),
            "destination_account": str(dst_ata),
            "mint": tok["mint"],
            "symbol": tok["symbol"],
            "amount": self.from_units(amount_u, tok["symbol"]),
            "decimals": tok["decimals"],
            "blockhash": str(blockhash),
        }

    async def _send(self, to: str, amount_u: int,
                    symbol: str | None = None) -> str:
        """Build, sign, broadcast one SPL transfer. Returns the signature.

        The mint comes from the token being moved. A USDC transfer and
        a USDT transfer are different instructions against different
        accounts, and using the wrong mint would move the wrong asset
        while looking successful.

        Not reached by any test that lacks a funded treasury: submit()
        refuses before this on signer or SOL balance.
        """
        import solders.compute_budget as cb
        import solders.instruction as ix
        import solders.transaction as tx_mod

        # The SPL Token instruction builders are not shipped by solders
        # or solana-py -- see chains/spl_instructions.py for why they are
        # built here instead.
        from . import spl_instructions as spl

        c = await self._get_client()
        tok = self.token(symbol)
        sender = _solders["Pubkey"].from_string(self._treasury_address())
        dest = _solders["Pubkey"].from_string(to)
        mint = _solders["Pubkey"].from_string(tok["mint"])
        program = _solders["Pubkey"].from_string(SPL_TOKEN_PROGRAM)
        system_prog = _solders["Pubkey"].from_string(SYSTEM_PROGRAM)

        async def ensure_ata(owner: _solders["Pubkey"]):
            if self._ata_policy != "create":
                return
            # Use the SAME derivation as ata_address(): the seed is
            # [owner, token_program, mint] and the PDA program is the
            # ATA program. Deriving it here with the token program as
            # the PDA program produced an address that is not the ATA,
            # and the chain rejected the create with InvalidSeeds
            # ("Associated address does not match seed derivation").
            # One derivation, one place.
            ata = _solders["Pubkey"].from_string(
                self.ata_address(str(owner), tok["mint"])
            )
            info = await c.get_account_info(ata)
            if info.value is None:
                blockhash = await self._fresh_blockhash()
                # solders 0.29 builds and signs in one call:
                # new_signed_with_payer(instructions, payer, keypairs,
                # blockhash). There is no new_with_blockhash, and
                # new_with_payer has no recent_blockhash parameter and
                # no way to attach a blockhash afterwards.
                t = tx_mod.Transaction.new_signed_with_payer(
                    [
                        spl.create_associated_token_account(
                            payer=sender, ata=ata, owner=owner, mint=mint,
                            system_program=system_prog,
                            token_program=program,
                        )
                    ],
                    sender,
                    [self._keypair()],
                    blockhash,
                )
                await c.send_transaction(t)
                # A created ATA must be visible before the transfer that
                # references it, so confirm it rather than racing ahead.
                for _ in range(20):
                    info = await c.get_account_info(ata)
                    if info.value is not None:
                        return
                    await asyncio.sleep(0.25)

        await ensure_ata(dest)

        # Both accounts through the ONE derivation. This used to be
        # derived inline with the token program as the PDA program,
        # which yields addresses that are not the ATAs -- the transfer
        # would then have referenced accounts that do not exist.
        src_ata = _solders["Pubkey"].from_string(
            self.ata_address(str(sender), tok["mint"])
        )
        dst_ata = _solders["Pubkey"].from_string(
            self.ata_address(str(dest), tok["mint"])
        )

        # Build, sign and broadcast -- with ONE retry on a stale
        # blockhash.
        #
        # A blockhash is valid for a short window (about 150 slots,
        # a few seconds). The public endpoint hands out hashes that
        # expire while the transaction is being prepared, and the
        # chain rejects the result with BlockhashNotFound. The
        # transaction never entered the mempool, so nothing was
        # sent -- the fix is a NEW blockhash and a NEW signature, not
        # a resend of the same one.
        #
        # Before this, a payment that hit a stale hash failed once
        # and the user was told it could not be sent.
        last = None
        for attempt in range(2):
            blockhash = await self._fresh_blockhash()
            t = tx_mod.Transaction.new_signed_with_payer(
                [
                    # The compute budget instruction lives in
                    # solders.compute_budget, not solders.instruction,
                    # and takes the unit count directly:
                    #   cb.set_compute_unit_limit(200_000)
                    cb.set_compute_unit_limit(200_000),
                    spl.transfer_checked(
                        source=src_ata,
                        mint=mint,
                        destination=dst_ata,
                        owner=sender,
                        amount=amount_u,
                        decimals=tok["decimals"],
                        program_id=program,
                    ),
                ],
                sender,
                [self._keypair()],
                blockhash,
            )
            try:
                resp = await c.send_transaction(t)
                sig = resp.value
                if sig is None:
                    raise RuntimeError(
                        "solana RPC accepted nothing and returned no signature")
                return str(sig)
            except Exception as e:
                last = e
                if not _is_stale_blockhash(e):
                    raise
                # The hash went stale. Ask for a new one and try once
                # more.
                await asyncio.sleep(0.4)
        raise last

    def _keypair(self):
        """The signing keypair, from the signer abstraction.

        The engine asks for it rather than holding it: the key bytes
        live inside the signer object, and the engine only ever sees
        the keypair handle it needs to hand to solders.
        """
        signer = self._signer
        if signer is None:
            raise NotSupported("no Solana signer configured")
        kp = getattr(signer, "keypair", None)
        if kp is None:
            raise NotSupported(
                "the configured signer cannot expose a keypair; "
                "solders needs it to build a signed transaction"
            )
        return kp()

    def _sign_tx(self, t):
        """Sign with the configured signer.

        The signer is a callable abstraction rather than a raw keypair so
        the key itself is never held by the engine, only the object that
        can use it.
        """
        signer = self._signer
        if signer is None:
            raise NotSupported("no Solana signer configured")
        return signer.sign_transaction(t)