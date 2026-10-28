"""Booking webhook (#435): after a new booking, POST its Voucher JSON to BOOKING_WEBHOOK_URL.

The booking is stored before the call, so a failed webhook never undoes or breaks it: the
failure is logged with the host and the error, and the user gets the voucher anyway. One POST
per booking of a session (cart.checkout_reply); a receiver tells a repeat by booking_id.
Empty BOOKING_WEBHOOK_URL: no call. Stdlib urllib: no new dependency.
"""

import logging
import os
import urllib.parse
import urllib.request

from api.contracts import Voucher

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 3.0


def _timeout() -> float:
    try:
        return float(os.environ.get("BOOKING_WEBHOOK_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
    except ValueError:
        log.warning("webhook: BOOKING_WEBHOOK_TIMEOUT_S is not a number, %s s", DEFAULT_TIMEOUT_S)
        return DEFAULT_TIMEOUT_S


def send_booking(voucher: Voucher) -> bool:
    """POST the voucher to the partner; True if the receiver answered 2xx."""
    url = os.environ.get("BOOKING_WEBHOOK_URL", "").strip()
    if not url:
        return False
    try:
        req = urllib.request.Request(
            url,
            data=voucher.model_dump_json().encode(),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "odin-zapros-webhook/1",
                "X-Booking-Id": voucher.booking_id,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            log.info("webhook: booking %s sent, HTTP %s", voucher.booking_id, resp.status)
            return True
    # Any failure of the receiver (HTTP 4xx/5xx, timeout, DNS, a bad URL) must not break a
    # stored booking: it is logged, not swallowed.
    except Exception as e:  # noqa: BLE001
        host = urllib.parse.urlsplit(url).netloc  # the path and the query may hold a token
        log.warning("webhook: booking %s not sent to %s: %r", voucher.booking_id, host, e)
        return False
