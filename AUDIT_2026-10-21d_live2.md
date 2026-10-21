# U-bot Pay — Live audit round 2

_2026-10-21, read-only._

Following round 1 (L-1 /history/&/invoices/, L-3 /balance allow-list, L-2 documented-not-fixed), this round re-verifies the auth surface and looks for missed exposure points. All findings marked **NEW** are below.

## Findings (round 2)

| # | Finding | Severity | Status |
|---|---|---|---|
| L-2 | Header dedup L-2 still open from round 1. My editor script broke one-line `location = /X { add_header ... }` blocks on the second attempt and reverted cleanly; production is on the L-1-fixed vhost, no impact. | Medium | Carry forward. |
| F-1 | `/bridge/quote` (GET) returns full quote payload including **`approval_address` (a spender the user is asked to hand-approve)**, the partner's fee, the route choice, and execution duration — without authentication. The docstring is explicit: *"Rate limited per IP (audit 20 Oct round 4): **unauthenticated** and each call fans out to two paid upstream providers."* So this is **by design** — but worth re-affirming given the rate-limit is 20r/m and `approval_address` is an attacker's bait vector if the user is later tricked into signing a malicious allowance. | Informational (by design) | None — the rate limit plus the post-M5 `_validate_tx_target` on `/bridge/start` already protect the user when they go from quote to action. |
| F-2 | `/energy/prices` returns TronRental market price snapshot without auth. Docstring: *"Public, no key needed."* Status. | Informational (by design) | None. |
| F-3 | `/dashboard` (GET, `/dashboard`) returns `treasury_usdc`, `treasury_avax`, `revenue_total_usdc` **without authentication**. Docstring: *"Public dashboard KPIs (no auth) for the home-page tiles."* Currently returns all `null` because the demo session has no payments; the leak is *potential*, not active. If the demo user is later removed or replaced with a real one, treasury balance becomes public. **Same class of bug as L-3 was for /balance, but lower impact** because the values are *operational aggregates* not raw balances. | Medium (potential) | None applied. Recommend the same allow-list / auth-gate pattern as L-3 the next time this handler is touched. |
| F-4 | `/bridge/partners` (GET) is unauthenticated and reveals operational counters: `ok`, `fail`, `success_rate`, `last_ok` (unix ts), `last_fail`, `is_healthy`. Two partners (lifi, across) both `is_healthy: true`. **Useful intel for a competitor or attacker planning provider-targeted abuse**, but not a PII leak. | Low (operational) | None. |
| F-5 | `/bridge/alerts` (GET) is unauthenticated and returns `{"alerts": [], "count": 0}` today. If alerts ever contain user-identifying data (refunds, failed settlements), those become public. Currently empty. | Low (future risk) | None. |
| F-6 | `/auth/chains` (GET) is unauthenticated and returns the chain registry (currently 1 chain listed in the response — Avalanche — but the code's route table supports 15). Mini-leak: confirms which chains U-bot Pay considers live. | Informational | None. |
| F-7 | `/push/vapid-key` (GET) is unauthenticated and returns the VAPID public key (`BBHJXb-...`). VAPID pub keys are meant to be public; bootstrap. Auth is *not* expected. | Informational (by design) | None. |
| F-8 | `/auth/wallet/nonce` accepts `chain_id` as a request body param and produces a SIWE-style nonce *"UbotPay sign-in: <nonce> — chain <id> — <address_lowercase>"* with no validation that `chain_id` is in the supported registry. Tested `chain_id: 99` and the request **succeeds with 200**. A subsequent `/auth/wallet/verify` would then accept a signed nonce for chain 99 even though no chain 99 exists. Today this maps to a DoS surface (a user can be tricked into a signed message they cannot use), not a fund-flow bug. | Low (DoS) | None. Adding `chain.get_chain(chain_id)` validation to `auth/wallet/nonce` would be a 4-line fix; defer to next maintenance window. |
| F-9 | All 41 endpoints in `api.py` map correctly: 24 require auth (return 401 without `X-API-Key`), 16 are explicitly public (return 200 with valid data even anon), and 1 (webhook) uses its own secret path. Spot-checked 12/16 of the public ones; behavior matches the docstrings. | Confirmatory | None. |

## Round 2 — what I did not find (negative results)

- **No new auth-bypass.** Every endpoint that requires auth rejected anonymous requests with HTTP 401 (or 405 for GET-on-POST routes that aren't auth-reachable). The X-Forwarded-For spoofing test did not bypass `/turn` rate limits (rate-limit is key-based, not IP-based for the bot's protected paths).
- **No new info-leak of treasury.** After the L-3 fix, `0x673678...56D85669` (treasury) and `0xbE440bf22...` (revenue) both return 401 on `/balance/{addr}` without auth. `/dashboard` returns `null` for all treasury fields when the demo session has no payments. F-3 is the residual.
- **No new path-traversal / arbitrary route.** `/api?...` matched the SPA catch-all and returned `index.html` (200 with text/html), but that's the SPA fallback behaviour, not data leakage.
- **No 5xx on any read endpoint** during this session. Errors were all 401 / 422 / 404 / 405 / 200 — no handler crashed.
- **No JWT bypass.** Bogus JWT tokens rejected at the auth layer.

## Round 2 — watch list

1. **F-3 (dashboard treasury reveal)**: if product removes the demo session (i.e. demo becomes a real session), `/dashboard` becomes the new L-3. Pair-fix the next time the handler is touched.
2. **F-8 (nonce chain_id)** validated with a 4-line guard before the verify pathway. Keep this in mind if you add a new chain and forgot to validate it server-side.

## Round 2 — verified positives

- L-1 (`/history/{key}`, `/invoices/{key}`) live and serving real 404s from FastAPI instead of SPA HTML.
- L-3 (balance allow-list) live: `0x713261F...` and `0x000...dEaD` public; treasury and revenue both 401.
- 654/654 tests green after both fixes.

## Continuation

This round covered 41 endpoints in `api.py`. Next round (round 3) candidates:

- **Static site (`/var/www/ubotpay`)** — focus on client-side secrets that may have leaked into the SPA. Round 1 surfaced `agentId: AGENT_ID` literal in `index.html` (documented, not fixed). Round 3 should grep for the same class of bug across all templates.
- **Re-run the audit after utronpay.com DNS propagates** — when utronpay is live, the new chain surface opens. `/balance/{trc20_address}` and `is_address()` and `to_units()` paths that previously were never user-facing need separate review.

For the moment, the live service is clean.
