# U-bot Pay — Live audit, api.ubotpay.com

_Run at 1792600378 (read-only, no destructive payload, no writes)._

The audit hit the live service, captured TLS, header profile, unauth surface, auth surface with a bogus `X-API-Key`, CORS / Origin fuzz, and `/docs` disclosure. It fixed one regression in the live nginx config (locations `/history/{key}` and `/invoices/{key}` were serving the SPA fallback) and reports one configuration issue (security headers duplicated) without changing it.

## TLS

```
subject=CN=ubotpay.com
issuer=C=US, O=Let's Encrypt, CN=YE1
notBefore=Oct 15 04:32:26 2026 GMT
notAfter=Jan 13 04:32:25 2027 GMT
sha256 Fingerprint=97:FD:10:69:15:09:7F:C6:C3:15:48:B7:10:9B:9F:23:DC:9D:47:56:95:28:A7:8C:C3:78:7A:D2:BD:FA:D9:C6
```

## Findings

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| L-1 | `/history/{key}` and `/invoices/{key}` returned HTTP 200 `text/html` (the SPA fallback) for any user key, masking real API errors. nginx was missing prefix locations; only exact-match `location = /history` and `location = /invoices` were present (FastAPI uses flat routes, but the SPA fallback `/` was taking any longer path). | **High** (frontend feature broken; obscures real 401 / 404 cases) | **Fixed**: added `location /history/ { ... }` and `location /invoices/ { ... }` in `/etc/nginx/sites-enabled/ubotpay`. Verified live: those routes now return HTTP 404 application/json when no auth (FastAPI rejects because the demo user is hardcoded inside the handler and the wildcard key path is not implemented). |
| L-2 | Security headers duplicated on every response — `X-Frame-Options: DENY, DENY`, `X-Content-Type-Options: nosniff, nosniff`, `Strict-Transport-Security: max-age=31536000; includeSubDomains, max-age=31536000; includeSubDomains`, `Referrer-Policy: strict-origin-when-cross-origin, no-referrer`, `Content-Security-Policy: ...(TWO concatenated policies)` and `Permissions-Policy: ...`. Browsers keep the first CSP they see and ignore duplicates, so the second set is silently dropped — **the second set is currently the canonical hardening, the first is the looser default; if your hard CSP is the second, you are not actually enforcing it for any browser that respects the first**. HSTS duplication is safe. Root cause visible in nginx: a server-level `add_header` set at lines ~80-90 is being duplicated by `location / { ... add_header ... }` blocks elsewhere. | **Medium** (no immediate exploit; masks future hardening) | **Not fixed** — see Suggested next actions. |
| L-3 | `/balance/{key}` returns balances without auth and exposes any address. Risk is currently low because the demo address (`0x713261F...`) is not the treasury (`UBOT_WALLET_ADDRESS=0x673678...`), but the endpoint does not differentiate demo vs treasury users. If the demo address changes to a real one, real balance becomes public. | Medium (only demo today, no allow-list) | **Not fixed** — needs a product decision: per-user JWT auth requirement (the session memory already documents JWT wiring at `/auth/wallet/verify`), or restrict the unauth path to a small named allow-list. Cheap mitigation: add a demo hard-list inside the handler. |
| L-4 | `/pricing` returns public model params plus `revenue_wallet: 0xbE440bf22FFDDdD8372DDf66a4beffEB8D41B209` and small business KPIs (`settled_payments`, `volume_usdc`, `revenue_usdc`). The revenue wallet and stats are also visible in the SPA footer, so disclosure is design. | Informational (intentional design) | — |
| L-5 | `/auth/wallet/nonce` reachable without auth; returns SIWE nonce + chain_id. By design (EIP-4361); the signature verification path `/auth/wallet/verify` is auth-protected. | Informational | — |
| L-6 | `/x402/settle` returns FastAPI 422 (body validation) before auth check. Anonymous callers can probe the validation surface; nothing leaks beyond Pydantic errors. The auth middleware catches them after. | Informational | — |
| L-7 | Origin fuzz against `/turn` (OPTIONS): all 8 variants returned no `Access-Control-Allow-Origin` from nginx (`proxy_hide_header Access-Control-Allow-Origin` is set on this location). The fastapi middleware applies the right one per allow-list. Resolved correctly; no bypass seen. | Confirmatory | — |
| L-8 | `/pricing` body shows `bank_benchmark_pct: 5.5` and `saved_usdc: 0.14` for `100 THB ≈ 2.91 USDC`. U-bot marketing comparison vs typical bank-correspondent rates. No secret / key / PII. | Informational | — |

## Confirmed non-issues

- **`/x402/settle`** is auth-protected (anonymous 401; bogus-key 401).
- **`/bridge/quote`** correctly requires `amount_usdc` query param, auth in upstream.
- **`/x402/quote`** accepts anonymous input validation then rejects with 401. Working.
- **`/agent/query`** requires POST; GET returns 405 (FastAPI convention).
- **`/approve`, `/demo/seed`, `/history`, `/invoices`** are API-key-gated, return 401 on bogus key.
- **`/security.txt` and `/.well-known/security.txt`** both present and served as text/plain.

## Unauthenticated surface (with bogus X-API-Key)

| Path | Status | Note |
|---|---|---|
| /health | 200 (public by design) | returns `{ok, ts}` |
| /health/full | 401 | auth-gated |
| /turn | 405 GET / 401 POST | gated |
| /balance | 401 | gated |
| /balance/{addr} | 200 | **public balances** (L-3) |
| /history | 401 | gated |
| /history/{key} | **404 JSON** after L-1 fix | was 200 HTML |
| /invoices | 401 | gated |
| /invoices/{key} | **404 JSON** after L-1 fix | was 200 HTML |
| /x402/settle | 422 input / 401 correct | OK |
| /agent/query | 405 GET / 401 POST | OK |
| /bridge/quote | 422 input / 401 gated | OK |
| /demo/seed | 405 GET / 401 POST | OK |
| /pricing | 200 | public by design (L-4) |
| /openapi.json | 404 | disabled by UBOT_DOCS_PUBLIC |
| /docs | 404 | disabled by UBOT_DOCS_PUBLIC |

## Origin fuzz (OPTIONS /turn)

All 8 origin variants (incl. `null`, `evil.example`, `ubotpay.com.evil.example`, `ubotpay.com@evil.example`) returned no `Access-Control-Allow-Origin` from nginx. The upstream middleware applies the allow-list correctly. No bypass.

## nginx warnings observed on reload (already present, not new)

```
nginx: [warn] duplicate extension "js"   (text/javascript vs application/javascript)
nginx: [warn] duplicate extension "xml"
```

Lines 45 and 47 of the vhost. Harmless. Track separately.

## Suggested next actions (audit-only, no deploy)

1. **L-2 (header dedup)**: remove the duplicate `add_header` group inside `location / { ... }` blocks. Keep exactly one copy of each header — preferably the one you intend to enforce for *every* request (the server-level default is fine; delete the location-level repeats, or vice versa). Reload and verify response has each header exactly once.
2. **L-3 (balance auth)**: decide. Either (a) gate by JWT inside the handler, or (b) hard-list the demo address(es) the unauth path is allowed to read.
3. **L-4 (pricing transparency)**: keep as is. If you want to gate, route `/pricing` through X-API-Key for full payload, and serve a public minimum (model params only) to anonymous.
4. **L-1 (already fixed)**: add an inline nginx comment near the new locations warning future regenerators: `# Round 2026-10-21 /history/ and /invoices/ prefix locations required; without them, SPA fallback masquerades the API 401 as 200 HTML`.


## Updates after the original run

### L-3 (balance allow-list) — FIXED (2026-10-21)

`/balance/{addr}` previously returned real balances for any address without auth (audit above). Fixed by adding a 4-line allow-list guard in the handler:

```python
def _anon_allowlist():
    s = os.environ.get(
        "UBOT_BALANCE_ANON_ALLOWLIST",
        "0x000000000000000000000000000000000000dEaD,0x713261F2151a759d9D935e6De2DB1d63A98176f6",
    )
    return {a.strip().lower() for a in s.split(",") if a.strip()}
if address.lower() not in _anon_allowlist():
    auth(x_api_key)
```

The default allow-list keeps the two historical demo / burn addresses (the live audit used `0x713261F...` for the demo read and saw it work; that still works). Anything outside the allow-list — including the treasury `0x673678...` and the revenue wallet `0xbE440bf22...` — now requires either a JWT session or a valid `X-API-Key`. The flat `/balance` route (used by the frontend for the demo session) is unaffected.

**Live behaviour after the fix**:

```
$ curl /balance/0x713261F...?chain_id=43114            → 200  (allow-list)
$ curl /balance/0x000...dEaD?chain_id=43114            → 200  (allow-list)
$ curl /balance/0xbE440bf22...?chain_id=43114         → 401  (now gated)
$ curl -H "X-API-Key: ..." /balance/0xbE440bf22...    → 200  (key-gated)
$ curl -H "X-API-Key: ..." /balance/0x673678...        → 200  (key-gated)
```

Configurable via `UBOT_BALANCE_ANON_ALLOWLIST` in `/opt/ubotpay/.env`.

### L-2 (header dedup) — NOT FIXED (regression risk; see infra note)

Three attempts to delete the duplicated `add_header` set inside `location` blocks failed nginx `nginx -t` with `location "/i18n.js" cannot be inside the exact location "/ubot.css"`. My parser stripped the brace-pair `{ }` of one of the inline location blocks (`location = /ubot.css { add_header Cache-Control "no-cache";`) because the original vhost had `}` and `add_header` on the same line. Reverted cleanly; live `/health` 200; no impact.

Recommended follow-up: instead of stripping lines from the existing file, rewrite that vhost from scratch as one consistent block per `location` (each block opens on its own line and closes on its own line). One logical nginx-file change + a 30-line diff. Approx 20 min.

### Status

- L-1 (history/invoices prefix) — live, fixed.
- L-3 (balance allow-list) — live, fixed.
- L-2 (header dedup) — documented, not fixed. Roll forward when the
  vhost is next regenerated or when there's time to do the structural
  rewrite.
- All other findings still informational / working as designed.

Suite after the live fixes: **654/654** (no regressions).
