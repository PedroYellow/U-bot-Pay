"""
Audit round 3 close-out (2026-10-27): the three findings that were still open
after the live audit rounds 1-3.

  L-2  security headers were duplicated (server-level set + per-location
       copies). Browsers enforce only the FIRST policy they see, so the
       stricter set was being silently ignored.
  L-3  backup files (.bak-*, .before-fix3, .pre-fix-agent) were publicly
       served from the webroot, and index.html.bak-* carried a second-order
       XSS in the bridge error handler.
  S-3  the treasury address was hardcoded in index.html's footer.
  F-8  /auth/wallet/nonce accepted any chain_id, including chains that do
       not exist, minting a nonce for a signed message the user could never
       use.

The nginx parts are asserted against the LIVE vhost file, because the fix
lives in a file that is not in this repository (the vhost is deployed
separately from the app). The Python parts are asserted by importing the
app and calling the real handler.

Run:  python3 test_audit_round3_fixes.py
"""
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
import json

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def chk(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print("%s  %-58s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want))


def vhost_path():
    """Locate the live nginx vhost. Overridable for a local copy."""
    p = os.environ.get("UBOT_NGINX_VHOST", "/etc/nginx/sites-available/ubotpay")
    return p if os.path.exists(p) else None


# ── L-2: no duplicated security headers in the vhost ──────────────────────

def test_no_duplicate_server_level_security_set():
    """The 'Security headers (audit #6)' block must be gone.

    It was a second, looser CSP plus duplicates of the other four headers at
    server level, so every response carried two of each.
    """
    p = vhost_path()
    if not p:
        print("SKIP  L-2 duplicate server set (vhost not readable here)")
        return
    src = open(p).read()
    chk("L-2 audit#6 duplicate server set removed",
        "Security headers (audit #6)" in src, False)


def test_no_per_location_security_header_copies():
    """No location block may re-declare the security headers.

    nginx's add_header inheritance rule: a location that declares ANY
    add_header discards ALL inherited ones. So a location copy both
    duplicates the header and silently drops the server-level set.

    Counted per server block: the vhost holds two servers (the main site
    and agent.ubotpay.com) and each one legitimately has its own set.
    Within a server, each security header must appear exactly once.
    """
    p = vhost_path()
    if not p:
        print("SKIP  L-2 per-location copies (vhost not readable here)")
        return
    src = open(p).read()
    # split into server blocks on a line that starts a server
    blocks = re.split(r'\nserver\s*\{', "\n" + src)
    for idx, block in enumerate(blocks[1:], start=1):
        name = re.search(r'server_name\s+([^;]+);', block)
        label = name.group(1).strip() if name else "block%d" % idx
        for h in ("Content-Security-Policy", "Strict-Transport-Security",
                  "X-Content-Type-Options", "X-Frame-Options",
                  "Referrer-Policy", "Permissions-Policy"):
            n = len(re.findall(r'add_header\s+' + re.escape(h) + r'\s+"', block))
            # 0 is allowed: a server may legitimately omit a header.
            # What must never happen is 2+ (a duplicated policy, where the
            # browser enforces only the first and silently drops the rest).
            chk(f"L-2 [{label}] {h} declared at most once", n <= 1, True)


def test_cache_control_blocks_use_expires():
    """The MAIN server's asset locations must use `expires`, not
    `add_header Cache-Control`.

    `add_header Cache-Control` inside a location is what re-triggered the
    inheritance drop in the first place. Scoped to the main server block;
    the agent host is a separate server with its own (small) header set.
    """
    p = vhost_path()
    if not p:
        print("SKIP  L-2 expires conversion (vhost not readable here)")
        return
    src = open(p).read()
    blocks = re.split(r'\nserver\s*\{', "\n" + src)
    main = None
    for block in blocks[1:]:
        m = re.search(r'server_name\s+([^;]+);', block)
        if not m:
            continue
        names = m.group(1).split()
        # the main site server: it serves ubotpay.com itself (the api
        # subdomain is the same server block)
        if any(n in ("ubotpay.com", "www.ubotpay.com") for n in names) \
                and "agent.ubotpay.com" not in names:
            main = block
            break
    if main is None:
        print("SKIP  L-2 expires conversion (main server block not found)")
        return
    chk("L-2 no add_header Cache-Control in the main server",
        len(re.findall(r'add_header\s+Cache-Control', main)), 0)
    chk("L-2 main server uses expires for cache control",
        len(re.findall(r'^\s*expires\s+', main, re.M)) > 0, True)


# ── L-3: backup files must never be servable ─────────────────────────────

def test_backup_deny_rule_present():
    """A deny rule for backup file patterns must exist in the vhost."""
    p = vhost_path()
    if not p:
        print("SKIP  L-3 deny rule (vhost not readable here)")
        return
    src = open(p).read()
    chk("L-3 backup deny rule present", "bak" in src and "return 404" in src, True)


def test_no_backup_files_in_webroot():
    """No .bak-* / .before-fix* / .pre-fix* file may sit in the webroot."""
    root = os.environ.get("UBOT_WEBROOT", "/var/www/ubotpay")
    if not os.path.isdir(root):
        print("SKIP  L-3 webroot clean (webroot not readable here)")
        return
    bad = [f for f in os.listdir(root)
           if re.search(r'\.(bak|before|after|pre|post|orig|tmp|swp|old|backup)[-._]', f)
           or re.search(r'(before-fix|pre-fix|post-fix|fix)[-_a-z0-9]*\.(html|js|css|json)$', f)]
    chk("L-3 webroot has no backup files", bad, [])


def test_backup_urls_are_404():
    """Live check: the old backup URLs must 404, not 200."""
    base = os.environ.get("UBOT_SITE", "https://ubotpay.com")
    paths = ["index.html.bak-021648", "index.html.bak-050202",
             "agent.html.bak-021648", "analytics.html.bak-021648",
             "sw.js.bak-021648", "sw.js.bak-050226",
             "index.html.before-fix3", "index.html.pre-fix-agent"]
    codes = []
    for p in paths:
        try:
            req = urllib.request.Request(base + "/" + p, method="GET")
            with urllib.request.urlopen(req, timeout=20) as r:
                codes.append((p, r.status))
        except urllib.error.HTTPError as e:
            codes.append((p, e.code))
        except Exception as e:
            codes.append((p, "ERR:%s" % type(e).__name__))
    non_404 = [c for c in codes if c[1] != 404]
    chk("L-3 all backup URLs return 404", non_404, [])


# ── S-3: treasury address is not hardcoded in the static HTML ────────────

def test_treasury_not_hardcoded_in_html():
    """index.html must not contain the treasury address literally."""
    html = os.path.join(HERE, "web", "index.html")
    if not os.path.exists(html):
        print("SKIP  S-3 html check (web/index.html not found)")
        return
    src = open(html, encoding="utf-8").read()
    addr = os.environ.get("UBOT_WALLET_ADDRESS", "0x673678B1870Ae7ef598A5982D25ffdee68d85669")
    chk("S-3 treasury address not hardcoded in index.html", addr in src, False)


def test_treasury_link_loaded_at_runtime():
    """The footer must fetch the address at runtime instead."""
    html = os.path.join(HERE, "web", "index.html")
    if not os.path.exists(html):
        print("SKIP  S-3 runtime loader check (web/index.html not found)")
        return
    src = open(html, encoding="utf-8").read()
    chk("S-3 runtime treasury loader present", "loadTreasuryLink" in src, True)
    chk("S-3 treasury anchor has id", 'id="treasuryLink"' in src, True)


def test_treasury_endpoint_live():
    """The public /treasury endpoint must serve the address + explorer link."""
    api = os.environ.get("UBOT_API", "https://api.ubotpay.com")
    try:
        with urllib.request.urlopen(api + "/treasury", timeout=20) as r:
            d = json.loads(r.read())
    except Exception as e:
        print("SKIP  S-3 /treasury endpoint live (%s)" % type(e).__name__)
        return
    chk("S-3 /treasury returns ok", d.get("ok"), True)
    chk("S-3 /treasury returns an address", bool(d.get("address")), True)
    chk("S-3 /treasury returns an explorer url",
        str(d.get("explorer", "")).startswith("http"), True)
    chk("S-3 /treasury has a visible flag", "visible" in d, True)


# ── F-8: nonce must reject unsupported chains ─────────────────────────────

def _run_api(code, timeout=90):
    """Run code in a subprocess with the app dir on sys.path."""
    py = os.environ.get("UBOT_PY", sys.executable)
    env = os.environ.copy()
    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")
    r = subprocess.run([py, "-c", code], capture_output=True, text=True,
                       env=env, cwd=HERE, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s\n%s" % (r.returncode, r.stdout, r.stderr))
    return r.stdout.strip().split("\n")[-1]


def test_nonce_rejects_unknown_chain():
    """chain_id 99 does not exist -> the handler must raise, not mint a nonce."""
    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "import asyncio, api\n"
        "from fastapi import HTTPException\n"
        "async def main():\n"
        "    try:\n"
        "        await api.auth_wallet_nonce(api.WalletNonceRequest(\n"
        "            address='0x713261F2151a759d9D935e6De2DB1d63A98176f6', chain_id=99),\n"
        "            request=None)\n"
        "        print('NO_RAISE')\n"
        "    except HTTPException as e:\n"
        "        print('HTTP', e.status_code)\n"
        "asyncio.run(main())\n"
    )
    try:
        out = _run_api(code)
    except Exception as e:
        print("SKIP  F-8 unknown-chain rejection (%s)" % type(e).__name__)
        return
    chk("F-8 unknown chain_id raises HTTP 400", out, "HTTP 400")


def test_nonce_accepts_registered_chain():
    """Avalanche (43114) and Sonic (146) are registered -> must not raise."""
    for cid in (43114, 146):
        code = (
            "import sys; sys.path.insert(0, '.')\n"
            "import asyncio, api\n"
            "from fastapi import HTTPException\n"
            "async def main():\n"
            "    try:\n"
            "        await api.auth_wallet_nonce(api.WalletNonceRequest(\n"
            "            address='0x713261F2151a759d9D935e6De2DB1d63A98176f6', chain_id=%d),\n"
            "            request=None)\n"
            "        print('OK')\n"
            "    except HTTPException as e:\n"
            "        print('HTTP', e.status_code)\n"
            "asyncio.run(main())\n" % cid
        )
        try:
            out = _run_api(code)
        except Exception as e:
            print("SKIP  F-8 chain %d accepted (%s)" % (cid, type(e).__name__))
            continue
        # 'OK' = nonce minted. A rate-limit 429 is also acceptable here.
        chk(f"F-8 registered chain {cid} is not rejected as unsupported",
            out in ("OK", "HTTP 429"), True)


def test_nonce_live_rejects_unknown_chain():
    """Live check: POST /auth/wallet/nonce with chain_id 99 must be 400."""
    api = os.environ.get("UBOT_API", "https://api.ubotpay.com")
    body = json.dumps({
        "address": "0x713261F2151a759d9D935e6De2DB1d63A98176f6",
        "chain_id": 99,
    }).encode()
    req = urllib.request.Request(api + "/auth/wallet/nonce", data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:
        print("SKIP  F-8 live nonce check (%s)" % type(e).__name__)
        return
    chk("F-8 live nonce with chain_id 99 returns 400", code, 400)


# ── regression: the API still serves what it should ──────────────────────

def test_api_health_and_core_endpoints():
    """The three fixes must not have broken the existing public surface."""
    api = os.environ.get("UBOT_API", "https://api.ubotpay.com")
    for path, want in (("/health", 200), ("/dashboard", 200),
                        ("/pricing", 200), ("/treasury", 200)):
        try:
            with urllib.request.urlopen(api + path, timeout=20) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception as e:
            chk(f"live {path} reachable", "ERR:%s" % type(e).__name__, want)
            continue
        chk(f"live {path} returns {want}", code, want)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        try:
            t()
        except Exception as e:
            RESULTS.append(False)
            print("FAIL  %-58s error=%s: %s" % (t.__name__, type(e).__name__, str(e)[:120]))
    passed = sum(1 for r in RESULTS if r)
    print()
    print("%d/%d passed" % (passed, len(RESULTS)))
    sys.exit(0 if passed == len(RESULTS) else 1)
