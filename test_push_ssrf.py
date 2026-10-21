"""
Regression tests for the push endpoint SSRF (audit 20 Oct round 13).

/push/subscribe checked that `endpoint` and `keys` were present but never that the
endpoint was a push service URL. push.send_push() hands that string to pywebpush,
which does `aiohttp_session.post(endpoint)` — an outgoing request to a URL the
caller chose. Any authenticated account could register an internal address and
have the server issue the request on the next notification fan-out.

Two things this file pins down deliberately:

  - Validation happens both at subscribe time and at send time. Send-time alone
    would leave every row written before the fix usable for its remaining lifetime.
  - The host check is not a bare substring or endswith. "https://evil.com/?x=fcm.
    googleapis.com" and "https://notfcm.googleapis.com.evil.com" both contain the
    service name without being it.

    python3 test_push_ssrf.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RESULTS = []
from push import validate_push_endpoint as V


def check(name, fn, want_ok=True):
    try:
        V(fn)
        got = True
    except ValueError:
        got = False
    ok = got == want_ok
    RESULTS.append(ok)
    verdict = "accept" if want_ok else "reject"
    label = "ACCEPTED" if got else "rejected"
    print(f"{'PASS' if ok else 'FAIL'}  {name:52} {label:8} (want {verdict})")


REAL = [
    ("https://updates.push.services.mozilla.com/wpush/v2/L-123abc",
     "Mozilla FCM endpoint"),
    ("https://web.push.apple.com/QxY-abc123", "Safari"),
    ("https://wns2-by3p.notify.windows.com/w/?token=abc", "Edge WNS"),
    ("https://fcm.googleapis.com/fcm/send/abc123", "Chrome FCM v1"),
]


def t_real_endpoints_accepted():
    for url, label in REAL:
        check(f"real: {label}", url, want_ok=True)


def t_internal_targets_rejected():
    """The actual exploit."""
    targets = [
        ("http://127.0.0.1:8091/report", "loopback, http"),
        ("http://localhost:8091/report", "loopback by name"),
        ("http://169.254.169.254/latest/meta-data/", "cloud metadata"),
        ("http://[::1]:8091/report", "ipv6 loopback"),
        ("http://10.0.0.1/admin", "private range"),
        ("http://192.168.1.1/", "private range"),
        ("https://127.0.0.1:8091/report", "loopback over https"),
    ]
    for url, label in targets:
        check(f"ssrf: {label}", url, want_ok=False)


def t_non_https_rejected():
    """A redirect-free https-only rule; http would let a network attacker in."""
    check("http scheme", "http://fcm.googleapis.com/fcm/send/a", want_ok=False)
    check("no scheme", "fcm.googleapis.com/fcm/send/a", want_ok=False)
    check("file scheme", "file:///etc/passwd", want_ok=False)
    check("gopher scheme", "gopher://x/_x", want_ok=False)


def t_suffix_confusion_rejected():
    """A bare endswith() would accept all three of these."""
    check("prefix trick",
          "https://evil-fcm.googleapis.com.attacker.net/fcm", want_ok=False)
    check("suffix trick",
          "https://fcm.googleapis.com.evil.net/fcm", want_ok=False)
    check("host in query",
          "https://evil.net/?x=fcm.googleapis.com", want_ok=False)
    check("host in userinfo",
          "https://fcm.googleapis.com@evil.net/fcm", want_ok=False)
    check("path segment only",
          "https://evil.net/fcm.googleapis.com/fcm", want_ok=False)


def t_empty_and_malformed_rejected():
    for url, label in [("", "empty string"),
                       ("https://", "no host"),
                       ("https:///fcm", "empty host"),
                       ("not a url at all", "not a url")]:
        check(f"malformed: {label}", url, want_ok=False)


def t_none_rejected():
    try:
        V(None)
        got = True
    except ValueError:
        got = False
    RESULTS.append(got is False)
    print(f"{'PASS' if got is False else 'FAIL'}  {'None rejected':52} "
          f"{'rejected' if not got else 'ACCEPTED':8} (want reject)")


def t_length_capped():
    check("absurdly long URL",
          "https://fcm.googleapis.com/" + "a" * 2000, want_ok=False)


def t_case_and_whitespace_handled():
    """urlparse does not normalise, so a naive comparison would miss these."""
    check("uppercase scheme",
          "HTTPS://FCM.GOOGLEAPIS.COM/fcm/send/a", want_ok=True)
    check("uppercase host",
          "https://FCM.googleapis.com/fcm/send/a", want_ok=True)


def truthy(name, got, want=True):
    """For assertions about the source, not about URL acceptance."""
    ok = (got == want)
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:52} got={got!r} want={want!r}")


def t_validated_in_both_places():
    push_src = open(os.path.join(HERE, "push.py"), encoding="utf-8").read()
    api_src = open(os.path.join(HERE, "api.py"), encoding="utf-8").read()
    truthy("validated when sending",
           "validate_push_endpoint(" in push_src, True)
    truthy("validated when subscribing",
           "push.validate_push_endpoint(" in api_src, True)
    send = push_src[push_src.index("async def send_push"):]
    truthy("send-time check is before the request",
           send.index("validate_push_endpoint(") < send.index("await webpush_async("), True)


if __name__ == "__main__":
    t_real_endpoints_accepted()
    t_internal_targets_rejected()
    t_non_https_rejected()
    t_suffix_confusion_rejected()
    t_empty_and_malformed_rejected()
    t_none_rejected()
    t_length_capped()
    t_case_and_whitespace_handled()
    t_validated_in_both_places()
    print("=" * 76)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
