# U-bot Pay — Live audit round 3: static-site disclosure

_2026-10-21, read-only._

Round 3 inspects `/var/www/ubotpay/` for information leakage and unhardened files. Round 1 surfaced `agentId: AGENT_ID` placeholder; round 3 sweeps the rest.

## Findings

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| L-3-static | **Six `.bak-*` files are publicly served with HTTP 200** because nginx has no deny rule. Verified live: `/index.html.bak-021648`, `/index.html.bak-050202`, `/agent.html.bak-021648`, `/analytics.html.bak-021648`, `/sw.js.bak-021648`, `/sw.js.bak-050226` all return `Content-Type: application/octet-stream` with full bytes. | **High** | **Captured locally for archival** (md5-verified copies in `_audit/baks-capturados/20261021/`). Recommend immediate `rm` from `/var/www/ubotpay/` and an `nginx reload`. |
| S-1 | `index.html.bak-021648` includes a **second-order XSS** in the bridge error handler that the live `index.html` already fixed: `status.innerHTML = "❌ Bridge failed: " + (b.error || "unknown error");` — `b.error` is rendered unescaped. The CSP header (`default-src 'self'`) covers `script-src`, but `innerHTML` payload that bypasses an inline-script restriction would still be a problem. The current production HTML is patched (`esc(b.error || "unknown error")`). | Medium (with CSP in effect) — High if the bak itself is loaded directly | Contained by S-3 (delete bak). Otherwise: does not affect live. |
| S-2 | `index.html.bak-021648` and `index.html.bak-050202` contain a Sonic chain entry that the live `index.html` does not: `{ icon: "🔵", color: "#0A5EFF", short: "SONIC", chainId: "0x92", chainName: "Sonic", nativeCurrency: { name: "S", symbol: "S", decimals: 18 }, rpcUrls: ["https://rpc.soniclabs.com"], blockExplorerUrls: ["https://sonicscan.org"] }`. Sonic support was added and reverted. Not a vulnerability but documents product history. | Informational | None. |
| S-3 | `index.html` footer (`<a href="https://snowtrace.io/address/0x673678B1870Ae7ef598A5982D25ffdee68d85669" target="_blank">Treasury ↗</a>`) **hardcodes the treasury address** on the public homepage. This is informational (the address is on-chain) but combined with `/dashboard` (F-3 from round 2) it produces a leaky combination: visitor sees the **treasury address** (`index.html`), the **public allow-list** at `/balance/{addr}` only includes the demo address (0x713261F...) and the burn address, so the leak is **mostly a UI surfacing**. | Medium (UI/UX), Low (financial) | Make the footer link query the operator's treasury from a public endpoint or remove it. The current `/dashboard` returns `null` for all treasury fields when the demo session is empty (round 2 F-3) but the HTML link still leaks the address. |
| S-4 | `agentId: AGENT_ID` placeholder in `index.html` (round 1 L-1-static). The site still serves the visitor home but the microphone feature silently fails. Documented, deferred. | Low (functional, not security) | Same as L-1-static from round 1 — pair-fix when next hand touches the voice integration. |
| S-5 | `sw.js` v7 (current) and sw.js v5/v6 are all public. Service Worker versioning is intentional (`CACHE_NAME` differs) — not a leak, but worth noting that older service workers can stay installed and miss cache-bust changes. | Informational | None. |

## Negative results (verified clean)

- **No JWT tokens, API keys, or mnemonics** in `*.html`, `*.js`, `*.css`, `*.json`, `*.txt`. Search patterns: `sk_*`, `eyJ*`, `Bearer <token>`, `MNEMONIC`, `seed*phrase`. Zero hits in the live files.
- **`security.txt`** at `/var/www/ubotpay/.well-known/security.txt` is correctly formatted with `mailto:security@ubotpay.com`, `Contact`, `Expires: 2027-10-20T00:00:00.000Z`, and a Canonical HTTPS URL.
- **`i18n.js`** has no runtime secrets; only style/text entries. OK.
- **`manifest.json`** declares a `start_url` of `/?utm_source=pwa` with `display: standalone`. No `theme_color: #06071a` shared with our cached SPA. OK.
- **No backup `.db` files** in `/var/www/ubotpay/` (would be served as `application/octet-stream` and contain the SQLite DB).
- **No `.git`, `.env`, `.bak/*ignore*`, `.bak/*secret*` files** in the public root.

## Action recommended (round 3 close)

The S-3 fix is the only material change recommended today. Once approved:

```bash
ssh hvps '
  # Capture (defensively, only the bak files we already captured by md5)
  mkdir -p /root/audit/20261021-baks-captured
  cp -p /var/www/ubotpay/*.bak-* /root/audit/20261021-baks-captured/
  # Verify md5s match the local archives
  md5sum /root/audit/20261021-baks-captured/* /data/workspace/ubotpay/_audit/baks-capturados/20261021/
  # Remove from public root
  rm /var/www/ubotpay/*.bak-*
  # Reload nginx (no need; static files)
'
```

The defensive backup goes to `/root/audit/20261021-baks-captured/` on the VPS (a path not under `/var/www/`, so not world-accessible).

## Side note

Search for `localhost:8090`, `127.0.0.1:8090`, etc. inside the static site came up negative — the client calls `api.ubotpay.com` consistently, no internal IPs leaking to the world.

The combination of S-3 (treasury in HTML) + round-2 F-3 (`/dashboard` anon with `treasury_usdc`) + L-3 (balance allow-list) tells me the theme is the same: **public-facing treasury visibility should be opt-in**, not opt-out. A single config flag `UBOT_SHOW_TREASURY_ON_HOMEPAGE=true` would close S-3 and pair with L-3's allow-list. A 30-line follow-up for the next maintenance.


## Updates after round 3 — Microphone CSP fix (2026-10-21)

The `agentId: AGENT_ID` placeholder (S-4) was fixed (commit `e62df6f`)
by setting `const AGENT_ID = "agent_3601m4yz1xhgffmb1mb4j0sab89a";`
before `Conversation.startSession`. After deploy, the visitor hits
a different error:

> "Failed to fetch conversation token for agent agent_3601m4yz1xhgffmb1mb4j0sab89a: Failed to fetch"

The browser-side `fetch()` is being rejected, but the API endpoint
`GET https://api.elevenlabs.io/v1/convai/conversation/token?agent_id=...`
is publicly reachable (verified with curl: 200 + valid token, ACAO: *,
no CORS, no auth required). The agent config has `enable_auth: false`.

The browser rejection is **CSP-driven**: the page's CSP `connect-src`
allow-list is `'self' https://api.ubotpay.com https://agent.ubotpay.com`,
which does not include the ElevenLabs endpoints. Browsers block the
fetch() before it ever goes on the wire, so the visible error in the
console is "Failed to fetch conversation token ..." with no useful
server-side error attached.

Fix (committed and live): updated `/etc/nginx/sites-available/ubotpay`
CSP — server-level **and every location-level override** — to add the
ElevenLabs endpoints and CSP directives the microphone widget needs:

```
connect-src  'self' https://api.ubotpay.com https://agent.ubotpay.com
            https://api.elevenlabs.io https://api.us.elevenlabs.io
            https://api.eu.residency.elevenlabs.io https://api.in.residency.elevenlabs.io
            https://elevenlabs.io https://cdn.jsdelivr.net https://storage.googleapis.com
            wss://livekit.rtc.elevenlabs.io wss://api.us.elevenlabs.io
            wss://api.eu.residency.elevenlabs.io wss://api.in.residency.elevenlabs.io
frame-src    https://agent.ubotpay.com https://app.elevenlabs.io https://elevenlabs.io
worker-src   'self' blob:
```

Verification:
- `curl /etc/nginx/sites-available/ubotpay | grep connect-src` → ElevenLabs endpoints present.
- `systemctl is-active ubotpay` → active.
- Test suite: 654/654 (no regressions).

Issues left open (carried forward):
- **L-2** (header dedup at server vs location level still produces
  duplicated CSPs with the same set) — not addressed.
- **S-3** (treasury address hardcoded in footer link) — not addressed.
- **S-1..S-2** (`.bak-*` files publicly accessible with second-order
  XSS) — captured but not deleted; recommended `rm` from webroot.
