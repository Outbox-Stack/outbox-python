# Changelog

## 0.2.0

Matches the Node.js SDK 0.5.

- `verify_webhook` returns a typed `WebhookEvent`. New `MessageEventData` and `ContactEventData` types cover `message.deferred` (soft bounce or delivery delay, not final) and the bounce fields `bounce_type`, `bounce_subtype`, `diagnostic`, `reason`, `soft_bounce_count`, `suppressed` and `test`. Runtime behaviour is unchanged: the new fields were already in the returned dict.

## 0.1.0

First release: `send`, `send_batch`, `get_message`, message paging and iteration, `cancel_message`, suppressions, `verify_webhook`, automatic retries with idempotency keys. Same behaviour as the Node.js SDK 0.4.
