# Changelog

## 0.3.0

Matches the Node.js SDK 0.6. Requires the matching API release for `message.suppressed` webhooks.

- `MessageEventData` gains `description` for `message.suppressed`, the new event for a message that was held back and never sent. `reason` is a suppression reason or `known_dead_address`.
- `SuppressionReason` includes `unverified` (Amazon's pre-send address check); `list_suppressions()` and `iter_suppressions()` return typed `Suppression` dicts. `remove_suppression()` removes `manual` and `unverified` suppressions.
- `list_messages()`, `list_messages_page()` and `iter_messages()` accept `stream` (`transactional` or `marketing`).
- `get_message()` returns a typed `MessageDetail`, with `events` and `problem` (a `DeliveryProblem`). Runtime behaviour is unchanged: these keys were already in the returned dict.

## 0.2.0

Matches the Node.js SDK 0.5.

- `verify_webhook` returns a typed `WebhookEvent`. New `MessageEventData` and `ContactEventData` types cover `message.deferred` (soft bounce or delivery delay, not final) and the bounce fields `bounce_type`, `bounce_subtype`, `diagnostic`, `reason`, `soft_bounce_count`, `suppressed` and `test`. Runtime behaviour is unchanged: the new fields were already in the returned dict.

## 0.1.0

First release: `send`, `send_batch`, `get_message`, message paging and iteration, `cancel_message`, suppressions, `verify_webhook`, automatic retries with idempotency keys. Same behaviour as the Node.js SDK 0.4.
