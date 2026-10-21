"""Outbox Stack email SDK for Python."""

from .client import VERSION, Outbox
from .errors import OutboxApiError, OutboxConnectionError, OutboxError, WebhookVerificationError
from .webhooks import ContactEventData, MessageEventData, WebhookEvent, verify_webhook

__version__ = VERSION
__all__ = ["Outbox", "OutboxError", "OutboxApiError", "OutboxConnectionError", "WebhookVerificationError", "verify_webhook", "WebhookEvent", "MessageEventData", "ContactEventData", "VERSION"]
