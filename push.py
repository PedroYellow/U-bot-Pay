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