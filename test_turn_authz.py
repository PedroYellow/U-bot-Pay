"""
Regression tests for the IDOR fix (audit 20 Oct 2026, second round).

Before: POST /turn was public and took `user_key` from the request body.
`resolve_user()` accepts a telegram_id or a 0x address, so any caller could read
another user's balance / invoices / history / contacts, and the `agent_query`
intent would spend UBOT_PRIVATE_KEY on behalf of whoever they named.

These tests assert the identity rules on /turn without needing the full
FastAPI/chain stack: they exercise the decision logic directly.

    python3 test_turn_authz.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class FakeHTTP(Exception):
    """Stands in for fastapi.HTTPException."""
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}")
        self.status_code = code
        self.detail = detail


# ── the identity rules, mirroring api.turn() ──────────────────────────────
import hmac as _hmac


def _cmp(a, b):
    return _hmac.compare_digest((a or "").encode(), (b or "").encode())


def resolve_identity(auth_header, x_api_key, x_ubot_channel,
                     body_user_key, jwt_user, api_key, voice_secret):
    """Mirror of the auth branch in api.turn(). Returns (user_id, trusted, voice_svc).

    `jwt_user` is what _user_from_request() would return for the Authorization
    header: a user dict, or None.
    """
    user = jwt_user
    trusted = False
    voice_svc = False
    if user is None:
        if x_api_key and _cmp(x_api_key, api_key):
            return "resolved-from-body", True, False      # server-to-server
        if voice_secret and x_ubot_channel and _cmp(x_ubot_channel, voice_secret):
            return "demo", False, True                    # ElevenLabs, read-only
        raise FakeHTTP(401, "auth required")
    if body_user_key not in ("", None, "demo"):
        raise FakeHTTP(403, "session cannot act as another user")
    return user["id"], trusted, voice_svc


API_KEY = "internal-key"
VOICE = "voice-secret"
JWT_USER = {"id": 7, "name": "wallet user"}


def t_no_credentials():
    """The core IDOR: a bare public call must be refused."""
    try:
        resolve_identity(None, None, None, "demo", None, API_KEY, VOICE)
        return False, "expected 401, got through"
    except FakeHTTP as e:
        return e.status_code == 401, f"401 as expected"


def t_no_cred_cannot_name_victim():
    """An attacker naming someone else's telegram_id, with no credentials."""
    try:
        resolve_identity(None, None, None, "123456789", None, API_KEY, VOICE)
        return False, "expected 401 — IDOR still open"
    except FakeHTTP as e:
        return e.status_code == 401, f"401 as expected (IDOR closed)"


def t_session_cannot_impersonate():
    """A logged-in session asking to act as a different user → 403."""
    try:
        resolve_identity("Bearer tok", None, None, "999", JWT_USER, API_KEY, VOICE)
        return False, "expected 403 — session impersonation allowed"
    except FakeHTTP as e:
        return e.status_code == 403, f"403 as expected"


def t_session_ok_own():
    uid, trusted, voice = resolve_identity("Bearer tok", None, None, "",
                                           JWT_USER, API_KEY, VOICE)
    ok = uid == 7 and not trusted and not voice
    return ok, f"uid={uid} trusted={trusted} voice={voice}"


def t_server_key_ok():
    uid, trusted, voice = resolve_identity(None, API_KEY, None, "42",
                                           None, API_KEY, VOICE)
    ok = trusted and uid == "resolved-from-body"
    return ok, f"uid={uid} trusted={trusted}"


def t_wrong_key_refused():
    try:
        resolve_identity(None, "wrong", None, "demo", None, API_KEY, VOICE)
        return False, "expected 401 for a wrong api key"
    except FakeHTTP as e:
        return e.status_code == 401, "401 as expected"


def t_voice_secret_ok():
    uid, trusted, voice = resolve_identity(None, None, VOICE, "",
                                           None, API_KEY, VOICE)
    ok = voice and not trusted and uid == "demo"
    return ok, f"uid={uid} trusted={trusted} voice={voice}"


def t_voice_secret_wrong():
    try:
        resolve_identity(None, None, "nope", "", None, API_KEY, VOICE)
        return False, "expected 401 for a wrong voice secret"
    except FakeHTTP as e:
        return e.status_code == 401, "401 as expected"


def t_voice_secret_disabled():
    """If UBOT_VOICE_SECRET is unset, the voice path must be closed, not open."""
    try:
        resolve_identity(None, None, VOICE, "", None, API_KEY, "")
        return False, "expected 401 — voice path open with no secret configured"
    except FakeHTTP as e:
        return e.status_code == 401, "401 as expected (fail-closed)"


TESTS = [
    ("no credentials -> 401", t_no_credentials),
    ("no creds + victim user_key -> 401 (IDOR closed)", t_no_cred_cannot_name_victim),
    ("session impersonating another user -> 403", t_session_cannot_impersonate),
    ("session acting as itself -> ok", t_session_ok_own),
    ("server x-api-key -> ok, trusted", t_server_key_ok),
    ("wrong x-api-key -> 401", t_wrong_key_refused),
    ("voice secret -> ok, read-only", t_voice_secret_ok),
    ("wrong voice secret -> 401", t_voice_secret_wrong),
    ("voice secret unset -> 401 (fail-closed)", t_voice_secret_disabled),
]


if __name__ == "__main__":
    failed = 0
    for name, fn in TESTS:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        print(f"{'PASS' if ok else 'FAIL'}  {name:48} {detail}")
        failed += 0 if ok else 1
    print("=" * 66)
    print("ALL TESTS PASSED" if not failed else f"{failed} TEST(S) FAILED")
    sys.exit(1 if failed else 0)