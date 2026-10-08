from __future__ import annotations

from typing import Any, Optional


class OutboxError(Exception):
    """Base class for every error the SDK raises."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        #: The idempotency key the failed request carried. Retry with it and the API will not send twice.
        self.idempotency_key: Optional[str] = None


class OutboxApiError(OutboxError):
    """The API answered with a non-2xx status, or (code ``invalid_response``) a 2xx body that couldn't be read.

    On a send, ``invalid_response`` usually means the message was accepted: retry with ``idempotency_key`` to find out.
    Branch on ``code``; new codes may appear.
    """

    def __init__(self, status: int, message: str, body: Any = None, request_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        parsed = body if isinstance(body, dict) else {}
        self.code: str = parsed.get("code") or "error"
        #: Quote this to support.
        self.request_id: Optional[str] = parsed.get("request_id") or request_id
        #: The server's Retry-After hint in seconds, when it sent one.
        self.retry_after: Optional[float] = None


class OutboxConnectionError(OutboxError):
    """No answer from the API after every attempt. The request may or may not have reached it."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        #: ``timeout`` or ``connection_error``.
        self.code = code


class WebhookVerificationError(OutboxError):
    """The webhook's signature or timestamp didn't check out. Answer the request with 400."""
