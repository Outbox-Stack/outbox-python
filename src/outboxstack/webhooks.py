from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Mapping, Optional, Union

from .errors import WebhookVerificationError


def _header(headers: Mapping[str, Any], name: str) -> Optional[str]:
    for key, value in headers.items():
        if key.lower() == name:
            if isinstance(value, (list, tuple)):
                return value[0] if value else None
            return value
    return None


def verify_webhook(
    payload: Union[bytes, str],
    headers: Mapping[str, Any],
    secret: str,
    *,
    tolerance_seconds: int = 300,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Verifies a signed webhook (Standard Webhooks) and returns the parsed event.

    Pass the raw request body exactly as received, not re-serialised JSON, and the request headers.
    Raises WebhookVerificationError on a bad signature or a timestamp outside the tolerance (default 5 minutes).
    """
    message_id = _header(headers, "webhook-id")
    timestamp = _header(headers, "webhook-timestamp")
    signatures = _header(headers, "webhook-signature")
    if not message_id or not timestamp or not signatures:
        raise WebhookVerificationError("Missing webhook-id, webhook-timestamp or webhook-signature header")
    try:
        sent_at = int(timestamp)
    except ValueError:
        raise WebhookVerificationError("Webhook timestamp is outside the allowed tolerance") from None
    if abs((time.time() if now is None else now) - sent_at) > tolerance_seconds:
        raise WebhookVerificationError("Webhook timestamp is outside the allowed tolerance")
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except (binascii.Error, ValueError):
        raise WebhookVerificationError("Webhook secret is not valid base64") from None
    body = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    expected = hmac.new(key, f"{message_id}.{timestamp}.".encode("utf-8") + body, hashlib.sha256).digest()
    for candidate in signatures.split(" "):
        version, _, encoded = candidate.partition(",")
        if version != "v1" or not encoded:
            continue
        try:
            signature = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            continue
        if hmac.compare_digest(signature, expected):
            return json.loads(body.decode("utf-8"))
    raise WebhookVerificationError("No matching webhook signature")
