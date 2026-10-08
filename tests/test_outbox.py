import base64
import hashlib
import hmac
import json
import unittest

from outboxstack import Outbox, OutboxApiError, OutboxConnectionError, OutboxError, WebhookVerificationError, verify_webhook

MESSAGE_ID = "550e8400-e29b-41d4-a716-446655440000"


class FakeTransport:
    """Replays queued responses and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": json.loads(body) if body else None})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        status, data, headers_out = (response + ({},))[:3] if len(response) == 2 else response
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        return status, headers_out, raw


def client(transport, **kwargs):
    return Outbox("mk_test", transport=transport, sleep=lambda s: None, **kwargs)


class SendTests(unittest.TestCase):
    def test_send_sets_headers_and_generated_idempotency_key(self):
        t = FakeTransport((202, {"id": MESSAGE_ID, "status": "queued", "duplicate": False}))
        result = client(t).send({"from": "a@acme.com", "to": "b@example.com", "subject": "Hi", "html": "<p>x</p>"})
        self.assertEqual(result["id"], MESSAGE_ID)
        call = t.calls[0]
        self.assertEqual((call["method"], call["url"]), ("POST", "https://outboxstack.app/v1/messages"))
        self.assertEqual(call["headers"]["Authorization"], "Bearer mk_test")
        self.assertTrue(call["headers"]["User-Agent"].startswith("outbox-python/"))
        self.assertEqual(len(call["headers"]["Idempotency-Key"]), 36)

    def test_retries_server_errors_with_the_same_key_and_honours_retry_after(self):
        delays = []
        t = FakeTransport((503, {"error": "busy"}, {"Retry-After": "2"}), TimeoutError(), (202, {"id": MESSAGE_ID, "status": "queued", "duplicate": False}))
        Outbox("mk_test", transport=t, sleep=delays.append).send({"from": "a@acme.com", "to": "b@example.com", "subject": "Hi", "text": "x"}, idempotency_key="order-1")
        self.assertEqual([c["headers"]["Idempotency-Key"] for c in t.calls], ["order-1"] * 3)
        self.assertEqual(delays[0], 2.0)

    def test_client_errors_are_not_retried_and_carry_code_and_request_id(self):
        t = FakeTransport((422, {"error": "domain is not verified", "code": "domain_not_verified", "request_id": "req_1"}))
        with self.assertRaises(OutboxApiError) as caught:
            client(t).send({"from": "a@acme.com", "to": "b@example.com", "subject": "Hi", "text": "x"})
        e = caught.exception
        self.assertEqual((e.status, e.code, e.request_id, str(e)), (422, "domain_not_verified", "req_1", "domain is not verified"))
        self.assertIsNotNone(e.idempotency_key)
        self.assertEqual(len(t.calls), 1)

    def test_gives_up_with_connection_error_after_retries(self):
        t = FakeTransport(OSError("refused"), OSError("refused"), OSError("refused"))
        with self.assertRaises(OutboxConnectionError) as caught:
            client(t).get_message(MESSAGE_ID)
        self.assertEqual(caught.exception.code, "connection_error")
        self.assertEqual(len(t.calls), 3)

    def test_unreadable_success_body_is_invalid_response(self):
        t = FakeTransport((202, b"not json"))
        with self.assertRaises(OutboxApiError) as caught:
            client(t, max_retries=0).send({"from": "a@acme.com", "to": "b@example.com", "subject": "Hi", "text": "x"})
        self.assertEqual(caught.exception.code, "invalid_response")

    def test_attachment_bytes_are_base64_encoded(self):
        t = FakeTransport((202, {"id": MESSAGE_ID, "status": "queued", "duplicate": False}))
        client(t).send({"from": "a@acme.com", "to": "b@example.com", "subject": "Hi", "text": "x", "attachments": [{"filename": "a.txt", "content": b"hello"}]})
        self.assertEqual(t.calls[0]["body"]["attachments"][0]["content"], base64.b64encode(b"hello").decode())

    def test_batch_is_retried_only_when_every_item_has_a_key(self):
        t = FakeTransport((503, {}), (503, {}))
        with self.assertRaises(OutboxApiError):
            client(t).send_batch([{"from": "a@acme.com", "to": "b@example.com", "subject": "Hi", "text": "x"}])
        self.assertEqual(len(t.calls), 1)
        t = FakeTransport((503, {}), (202, {"accepted": 1, "failed": 0, "results": []}))
        client(t).send_batch([{"from": "a@acme.com", "to": "b@example.com", "subject": "Hi", "text": "x", "idempotencyKey": "k1"}])
        self.assertEqual(len(t.calls), 2)
        with self.assertRaises(OutboxError):
            client(t).send_batch([])


class ReadTests(unittest.TestCase):
    def test_iter_messages_follows_pages(self):
        t = FakeTransport((200, {"messages": [{"id": "1"}, {"id": "2"}], "next": "c1"}), (200, {"messages": [{"id": "3"}], "next": None}))
        ids = [m["id"] for m in client(t).iter_messages(status="bounced")]
        self.assertEqual(ids, ["1", "2", "3"])
        self.assertEqual(t.calls[0]["url"], "https://outboxstack.app/v1/messages?status=bounced")
        self.assertEqual(t.calls[1]["url"], "https://outboxstack.app/v1/messages?status=bounced&after=c1")

    def test_remove_suppression_escapes_the_address_and_is_not_retried(self):
        t = FakeTransport((503, {"error": "busy"}))
        with self.assertRaises(OutboxApiError):
            client(t).remove_suppression("a+b@example.com")
        self.assertEqual(t.calls[0]["url"], "https://outboxstack.app/v1/suppressions/a%2Bb%40example.com")
        self.assertEqual(len(t.calls), 1)

    def test_rejects_bad_ids_and_settings(self):
        with self.assertRaises(OutboxError):
            client(FakeTransport()).get_message("not-a-uuid")
        for bad in ("http://outboxstack.app", "https://outboxstack.app/v1", "https://user:pw@outboxstack.app"):
            with self.assertRaises(OutboxError):
                Outbox("k", base_url=bad)
        Outbox("k", base_url="http://localhost:3000")


class WebhookTests(unittest.TestCase):
    secret = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()

    def signed(self, body, timestamp="1700000000", msg_id="msg_1"):
        key = base64.b64decode(self.secret[6:])
        sig = base64.b64encode(hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()).decode()
        return {"Webhook-Id": msg_id, "Webhook-Timestamp": timestamp, "Webhook-Signature": "v1,bad v1," + sig}

    def test_valid_signature_returns_the_event(self):
        body = b'{"type":"message.bounced","created_at":"2026-10-08T10:00:00Z","data":{"to":"a@b.co","bounce_type":"hard"}}'
        event = verify_webhook(body, self.signed(body), self.secret, now=1700000100)
        self.assertEqual((event["type"], event["data"]["bounce_type"]), ("message.bounced", "hard"))

    def test_rejects_tampering_old_timestamps_and_missing_headers(self):
        body = b'{"type":"message.sent"}'
        with self.assertRaises(WebhookVerificationError):
            verify_webhook(body + b" ", self.signed(body), self.secret, now=1700000000)
        with self.assertRaises(WebhookVerificationError):
            verify_webhook(body, self.signed(body), self.secret, now=1700000000 + 301)
        with self.assertRaises(WebhookVerificationError):
            verify_webhook(body, {}, self.secret)


if __name__ == "__main__":
    unittest.main()
