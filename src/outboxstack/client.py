from __future__ import annotations

import base64
import json
import os
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Tuple

from .errors import OutboxApiError, OutboxConnectionError, OutboxError

VERSION = "0.2.0"
DEFAULT_BASE_URL = "https://outboxstack.app"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

#: (method, url, headers, body, timeout) -> (status, headers, body). Raises socket.timeout or OSError when there's no answer.
Transport = Callable[[str, str, Dict[str, str], Optional[bytes], float], Tuple[int, Mapping[str, str], bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _urllib_transport(method: str, url: str, headers: Dict[str, str], body: Optional[bytes], timeout: float) -> Tuple[int, Mapping[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _opener.open(request, timeout=timeout) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()
    except urllib.error.URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            raise TimeoutError() from error
        raise OSError(str(error.reason)) from error


def _check_key(key: Optional[str]) -> None:
    if key is not None and (not isinstance(key, str) or not key.strip() or len(key) > 200):
        raise OutboxError("idempotency_key must contain 1-200 characters")


def _check_email(email: Any) -> None:
    if not isinstance(email, str) or "@" not in email or len(email) > 254:
        raise OutboxError("A valid email address is required")


def _query(params: Dict[str, Any]) -> str:
    q = {k: v for k, v in params.items() if v is not None and v != ""}
    return "?" + urllib.parse.urlencode(q) if q else ""


def _encode(message: Dict[str, Any]) -> Dict[str, Any]:
    """Base64-encodes attachment content given as bytes."""
    attachments = message.get("attachments")
    if not attachments:
        return message
    out = dict(message)
    out["attachments"] = [
        {**a, "content": base64.b64encode(a["content"]).decode("ascii")} if isinstance(a.get("content"), (bytes, bytearray)) else a
        for a in attachments
    ]
    return out


def _retry_after(value: Optional[str]) -> Optional[float]:
    """Retry-After as delay-seconds or an HTTP date, in seconds from now."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        return max(parsedate_to_datetime(value).timestamp() - time.time(), 0.0)
    except (TypeError, ValueError):
        return None


class Outbox:
    """Server-side Outbox Stack client. No dependencies outside the standard library.

    Requests that can't cause a duplicate send are retried with backoff (see ``max_retries``).
    Every send carries an idempotency key, generated when you don't pass one, so a retry never sends twice.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = 10.0,
        max_retries: int = 2,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        api_key = api_key or os.environ.get("OUTBOX_API_KEY")
        if not api_key:
            raise OutboxError("An API key is required: pass api_key or set OUTBOX_API_KEY")
        base_url = base_url or os.environ.get("OUTBOX_BASE_URL") or DEFAULT_BASE_URL
        url = urllib.parse.urlsplit(base_url)
        if url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise OutboxError("base_url must be the platform origin")
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")):
            raise OutboxError("Use HTTPS outside localhost")
        if not timeout or timeout <= 0:
            raise OutboxError("timeout must be positive")
        if not isinstance(max_retries, int) or not 0 <= max_retries <= 10:
            raise OutboxError("max_retries must be an integer from 0 to 10")
        self._api_key = api_key
        self._base_url = f"{url.scheme}://{url.netloc}"
        self._timeout = timeout
        self._max_retries = max_retries
        self._transport = transport or _urllib_transport
        self._sleep = sleep

    def send(self, message: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """Accepts a message into the queue. Accepted is not delivered: track it with get_message() or webhooks.

        ``message`` uses the API's field names: ``from``, ``to``, ``subject``, ``html``/``text`` or
        ``template`` with ``data``, and optionally ``fromName``, ``replyTo``, ``cc``, ``bcc``, ``headers``,
        ``attachments`` (``content`` as bytes or base64) and ``metadata``.
        """
        _check_key(idempotency_key)
        return self._request("POST", "/v1/messages", body=_encode(message), idempotency_key=idempotency_key or str(uuid.uuid4()), retryable=True)

    def send_batch(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Sends up to 100 messages in one request. Items fail independently; check each result.

        Retried only when every item carries an ``idempotencyKey``.
        """
        if not isinstance(messages, list) or not 1 <= len(messages) <= 100:
            raise OutboxError("send_batch takes 1 to 100 messages")
        for m in messages:
            _check_key(m.get("idempotencyKey"))
        retryable = all("idempotencyKey" in m for m in messages)
        return self._request("POST", "/v1/messages/batch", body={"messages": [_encode(m) for m in messages]}, retryable=retryable)

    def get_message(self, message_id: str) -> Dict[str, Any]:
        if not isinstance(message_id, str) or not _UUID.match(message_id):
            raise OutboxError("A message UUID is required")
        return self._request("GET", "/v1/messages/" + message_id, retryable=True)

    def list_messages_page(self, *, status: Optional[str] = None, to: Optional[str] = None, limit: Optional[int] = None, after: Optional[str] = None) -> Dict[str, Any]:
        """One page, most recent first: ``{"messages": [...], "next": ...}``. Pass ``next`` back as ``after`` until it's None."""
        return self._request("GET", "/v1/messages" + _query({"status": status, "to": to, "limit": limit, "after": after}), retryable=True)

    def list_messages(self, **options: Any) -> List[Dict[str, Any]]:
        """The newest messages. For older ones use list_messages_page() or iter_messages()."""
        return self.list_messages_page(**options)["messages"]

    def iter_messages(self, *, after: Optional[str] = None, **options: Any) -> Iterator[Dict[str, Any]]:
        """Every matching message, most recent first, fetched a page at a time."""
        while True:
            page = self.list_messages_page(after=after, **options)
            yield from page["messages"]
            after = page.get("next")
            if not after:
                return

    def cancel_message(self, message_id: str) -> Dict[str, Any]:
        """Stops a message that hasn't started sending. Raises OutboxApiError with code ``not_cancellable`` once it has. Safe to retry."""
        if not isinstance(message_id, str) or not _UUID.match(message_id):
            raise OutboxError("A message UUID is required")
        return self._request("POST", f"/v1/messages/{message_id}/cancel", retryable=True)

    def list_suppressions_page(self, *, limit: Optional[int] = None, after: Optional[str] = None) -> Dict[str, Any]:
        """One page, newest first. Suppression calls need a full-access API key."""
        return self._request("GET", "/v1/suppressions" + _query({"limit": limit, "after": after}), retryable=True)

    def list_suppressions(self, **options: Any) -> List[Dict[str, Any]]:
        return self.list_suppressions_page(**options)["suppressions"]

    def iter_suppressions(self, *, after: Optional[str] = None, **options: Any) -> Iterator[Dict[str, Any]]:
        while True:
            page = self.list_suppressions_page(after=after, **options)
            yield from page["suppressions"]
            after = page.get("next")
            if not after:
                return

    def add_suppression(self, email: str) -> Dict[str, Any]:
        """Stops all mail to an address. Adding one that's already suppressed succeeds and keeps its reason."""
        _check_email(email)
        return self._request("POST", "/v1/suppressions", body={"email": email}, retryable=True)

    def remove_suppression(self, email: str) -> Dict[str, Any]:
        """Removes a ``manual`` suppression. Bounces, complaints and unsubscribes can't be removed (code ``conflict``).

        Not retried automatically: a retry after a lost response would report ``not_found`` for an address that was removed.
        """
        _check_email(email)
        return self._request("DELETE", "/v1/suppressions/" + urllib.parse.quote(email, safe=""), retryable=False)

    def _request(self, method: str, path: str, *, body: Any = None, idempotency_key: Optional[str] = None, retryable: bool) -> Any:
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json", "User-Agent": f"outbox-python/{VERSION}"}
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        def fail(error: OutboxError) -> OutboxError:
            error.idempotency_key = idempotency_key
            return error

        attempt = 0
        while True:
            can_retry = retryable and attempt < self._max_retries
            try:
                status, response_headers, raw = self._transport(method, self._base_url + path, headers, payload, self._timeout)
            except (TimeoutError, socket.timeout, OSError) as error:
                if not can_retry:
                    timed_out = isinstance(error, (TimeoutError, socket.timeout))
                    message = f"Outbox did not respond within {self._timeout:g}s" if timed_out else "Could not reach Outbox"
                    raise fail(OutboxConnectionError(message, "timeout" if timed_out else "connection_error")) from error
                self._sleep(self._delay(attempt))
                attempt += 1
                continue
            try:
                data = json.loads(raw.decode("utf-8")) if raw else None
            except (ValueError, UnicodeDecodeError):
                data = None
            request_id = response_headers.get("X-Request-Id") or response_headers.get("x-request-id")
            retry_after = _retry_after(response_headers.get("Retry-After") or response_headers.get("retry-after"))
            ok = 200 <= status < 300
            if ok and data is not None:
                return data
            if can_retry and (ok or status in (408, 429) or status >= 500):
                self._sleep(self._delay(attempt, retry_after))
                attempt += 1
                continue
            if ok:
                error = OutboxApiError(status, f"Outbox API returned HTTP {status} with an unreadable body", None, request_id)
                error.code = "invalid_response"
            else:
                message = data.get("error") if isinstance(data, dict) and data.get("error") else f"Outbox API returned HTTP {status}"
                error = OutboxApiError(status, message, data, request_id)
            error.retry_after = retry_after
            raise fail(error)

    @staticmethod
    def _delay(attempt: int, retry_after: Optional[float] = None) -> float:
        """Retry-After when the server gives one (capped at 30s), else exponential backoff with full jitter."""
        if retry_after is not None:
            return min(retry_after, 30.0)
        return random.random() * min(0.5 * 2**attempt, 8.0)
