# outboxstack

Python client for [Outbox Stack](https://outboxstack.app) email. Python 3.9+, no dependencies.

```sh
pip install outboxstack
```

Create a **Sending only** API key under Developers in the dashboard and set it as `OUTBOX_API_KEY`. For tests and staging, create a **Test** key instead: it checks every message but delivers nothing.

## Send

```python
from outboxstack import Outbox

outbox = Outbox()  # reads OUTBOX_API_KEY; base URL defaults to https://outboxstack.app

result = outbox.send({
    "from": "billing@your-verified-domain.com",
    "fromName": "Your app",
    "to": "Ada Obi <ada@example.com>",   # or a list
    "replyTo": "support@your-verified-domain.com",
    "subject": "Your invoice",
    "html": "<p>Invoice attached.</p>",
    "attachments": [{"filename": "invoice.pdf", "content": pdf_bytes}],
    "metadata": {"invoice_id": "inv_123"},
}, idempotency_key=f"invoice:{invoice.id}")

result["id"]          # the first recipient's message id
result["recipients"]  # one entry per recipient when there's more than one
```

Fields use the [API's names](https://outboxstack.app/openapi.json): `to`, `cc`, `bcc` (a string or a list, 50 recipients in total), `fromName`, `toName`, `replyTo`, `subject`, `html`, `text`, `template` with `data`, `headers` (20), `metadata` (10 strings) and `attachments` (10 files, 10 MB). Attachment `content` can be raw `bytes`; the client encodes it.

`send()` returns once the message is queued, not delivered. Each recipient becomes its own message with its own id, status and bounce tracking.

Every send carries an idempotency key, generated when you don't pass one, so an automatic retry never sends twice. Pass your own to stay safe across process restarts too.

Also: `send_batch(messages)` (1–100 messages, each may carry `idempotencyKey`) and `get_message(id)`.

## Find messages

```python
recent = outbox.list_messages(status="bounced", limit=100)   # newest 100

for m in outbox.iter_messages(status="bounced", to="@example.com"):
    print(m["id"], m["to_email"], m["created_at"])          # every match, a page at a time
```

To page by hand, call `list_messages_page()` and pass its `next` back as `after` until it's `None`.

## Cancel, suppress

```python
from outboxstack import OutboxApiError

try:
    outbox.cancel_message(message_id)
except OutboxApiError as e:
    if e.code != "not_cancellable":   # it already started sending
        raise

outbox.add_suppression("ada@example.com")       # needs a full-access key
outbox.remove_suppression("ada@example.com")    # manual suppressions only
for s in outbox.iter_suppressions():
    print(s["email"], s["reason"])
```

## Errors

| Exception | When |
| --- | --- |
| `OutboxApiError` | The API refused the request. Branch on `e.code` (`domain_not_verified`, `rate_limited`, `quota_exceeded`, …); quote `e.request_id` to support. |
| `OutboxConnectionError` | No answer after every retry (`e.code` is `timeout` or `connection_error`). The request may have arrived: retry with `e.idempotency_key`. |
| `OutboxError` | Bad input caught before any request, such as an invalid message id. |

Timeouts, connection errors, 408, 429 and 5xx are retried twice with backoff, honouring `Retry-After` (`max_retries=`, `timeout=` seconds per attempt).

## Webhooks

```python
from outboxstack import verify_webhook, WebhookVerificationError

# Flask example: pass the raw body, not re-serialised JSON.
@app.post("/hooks/outbox")
def outbox_hook():
    try:
        event = verify_webhook(request.get_data(), request.headers, os.environ["OUTBOX_WEBHOOK_SECRET"])
    except WebhookVerificationError:
        return "", 400
    if event["type"] == "message.bounced":            # a hard bounce: final
        mark_undeliverable(event["data"]["to"])
    # message.deferred is a soft bounce or delay: not final, so don't give up on the address.
    return "", 200
```

Events can arrive more than once: deduplicate on the `webhook-id` header. See [the event reference](https://outboxstack.app/docs/#events).

## Licence

MIT
