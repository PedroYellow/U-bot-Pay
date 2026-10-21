"""Ubotpay Web Push notifications.

Uses the Web Push Protocol (RFC 8030) with VAPID authentication
(https://datatracker.ietf.org/doc/html/rfc8292). No external service —
we sign each push ourselves with the VAPID private key.

The browser's Push Service (managed by Google for Chrome, Mozilla for
Firefox, etc.) holds the encrypted payload and tries to deliver it to
the user's device. We never see the message content, only the
endpoint URL the user subscribed with.
"""
import os
import json
from urllib.parse import urlparse
import time
import asyncio
import logging
from typing import Optional

log = logging.getLogger("ubotpay.push")

# Pull VAPID keys from env. If they're missing the module becomes a no-op.
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@ubotpay.com")

# Lazy import — pywebpush may not be installed in all environments.
_webpush = None
# Push endpoints must be https and on a known browser push service.
#
# Audit 20 Oct round 13: /push/subscribe validated that endpoint and keys were
# present but never that the endpoint was a push service URL. pywebpush then does
# `session.post(endpoint)` with whatever it was handed, so any authenticated
# account could register http://127.0.0.1:8091/report (or the cloud metadata
# address) and have the server issue the request on the next notification fan-out.
# The response body is not returned to the caller, but the request itself is the
# exploit: it reaches anything the server can reach, from inside.
#
# Real push endpoints are issued by the browser vendors and are always https.
_ALLOWED_PUSH_HOST_SUFFIXES = (
    ".push.services.mozilla.com",   # Firefox
    ".notify.windows.com",           # Edge
    ".web.push.apple.com",           # Safari
    "fcm.googleapis.com",            # Chrome (FCM v1)
    "updates.push.services.mozilla.com",
)


def validate_push_endpoint(endpoint: str) -> str:
    """Return the endpoint if it is an https URL on a known push service.

    Raises ValueError otherwise. Checked both when the subscription is stored and
    again when it is used, so a row written before this existed cannot be used to
    make the server issue requests.
    """
    if not endpoint or not isinstance(endpoint, str):
        raise ValueError("endpoint must be a non-empty string")
    if len(endpoint) > 1000:
        raise ValueError("endpoint too long")
    try:
        u = urlparse(endpoint)
    except Exception as e:
        raise ValueError("endpoint is not a valid URL") from e
    # scheme and netloc are compared lowercased: urlparse does not normalise, and
    # "HTTPS://" or a host with trailing whitespace would otherwise slip past.
    if (u.scheme or "").lower() != "https":
        raise ValueError("push endpoint must be https")
    host = (u.hostname or "").lower()
    if not host:
        raise ValueError("push endpoint has no host")
    # A bare suffix match would accept "evil-push.services.mozilla.com", so the
    # host must either be the service itself or end in "." + suffix.
    for suffix in _ALLOWED_PUSH_HOST_SUFFIXES:
        bare = suffix.lstrip(".")
        if host == bare or host.endswith("." + bare):
            return endpoint
    raise ValueError("endpoint is not a recognised browser push service")


def _get_webpush():
    global _webpush
    if _webpush is None:
        try:
            from pywebpush import webpush_async, WebPushException
            _webpush = (webpush_async, WebPushException)
        except ImportError:
            log.warning("pywebpush not installed; push notifications disabled")
            _webpush = (None, None)
    return _webpush


def is_configured() -> bool:
    """Return True if VAPID keys are present and push can be sent."""
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)


async def send_push(subscription_info: dict, title: str, body: str,
                    url: str = "/", tag: str | None = None,
                    icon: str = "/icon-192.png") -> bool:
    """Send a single push notification to one subscription.

    Args:
        subscription_info: {endpoint, keys: {p256dh, auth}} from the browser
        title: notification title (bold)
        body: notification body
        url: URL to open when the user clicks the notification
        tag: optional tag to replace existing notifications
        icon: notification icon URL

    Returns:
        True if the push service accepted the message (HTTP 2xx),
        False if rejected (subscription dead, etc.).
    """
    if not is_configured():
        log.debug("push not configured (VAPID keys missing)")
        return False
    # Re-validate here as well as at subscribe time. Rows written before this check
    # existed are still in the database, and validating only on insert would leave
    # them usable for as long as they lived.
    try:
        validate_push_endpoint((subscription_info or {}).get("endpoint") or "")
    except ValueError as e:
        log.warning("refusing push to a non-push-service endpoint: %s", e)
        return False
    webpush_async, WebPushException = _get_webpush()
    if webpush_async is None:
        return False

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "tag": tag or "",
        "icon": icon,
    })

    try:
        # TTL=86400 (24h). If the user's device is offline the push
        # service holds the message for up to a day.
        # urgency=high tells the push service to deliver quickly.
        await webpush_async(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},
            ttl=86400,
            urgency="high",
        )
        return True
    except WebPushException as e:
        # 404 / 410 = subscription is dead (user unsubscribed or cleared
        # site data). Caller should clean it up.
        log.info("push rejected (%s): %s", e.response and e.response.status_code, str(e)[:100])
        return False
    except Exception as e:
        log.warning("push failed: %s", e)
        return False


async def fan_out_push(user_subscriptions: list[dict], title: str, body: str,
                       url: str = "/", tag: str | None = None) -> int:
    """Send the same notification to all of a user's subscriptions.

    Returns the number of subscriptions that accepted the push.
    """
    if not user_subscriptions:
        return 0
    tasks = [
        send_push(sub, title=title, body=body, url=url, tag=tag)
        for sub in user_subscriptions
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok = sum(1 for r in results if r is True)
    return ok