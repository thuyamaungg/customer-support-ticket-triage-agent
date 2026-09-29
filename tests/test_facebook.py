import dataclasses
import hashlib
import hmac
import json

import httpx
import pytest
from sqlalchemy import func, select

from app.channels import ADAPTERS
from app.channels import facebook as fb_module
from app.channels.base import ChannelError
from app.channels.facebook import FacebookAdapter, split_text
from app.models import Message, MessageDirection, Ticket, TriageResult

SECRET = "test-app-secret"
VERIFY = "my-verify-token"
PAGE_TOKEN = "PAGE-TOKEN-123"


@pytest.fixture(autouse=True)
def fb_settings(monkeypatch):
    patched = dataclasses.replace(fb_module.settings, fb_app_secret=SECRET, fb_verify_token=VERIFY,
                                  fb_page_access_token=PAGE_TOKEN)
    monkeypatch.setattr(fb_module, "settings", patched)
    return patched


@pytest.fixture
def sent(monkeypatch):
    """Capture messages the app sends to Facebook instead of calling the real API."""
    outbox = []
    monkeypatch.setattr(ADAPTERS["facebook"], "send_message", lambda user, text: outbox.append((user, text)))
    return outbox


def event(sender="PSID-1", text="My laptop won't turn on", mid="mid.1", **message_extra):
    return {"sender": {"id": sender}, "recipient": {"id": "PAGE"}, "timestamp": 1,
            "message": {"mid": mid, "text": text, **message_extra}}


def payload(*events):
    return {"object": "page", "entry": [{"id": "PAGE", "time": 1, "messaging": list(events)}]}


def post_signed(client, data, secret=SECRET):
    body = json.dumps(data).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhook/facebook", content=body,
                       headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature})


def count(session_factory, model):
    with session_factory() as s:
        return s.scalar(select(func.count(model.id)))


# ---- setup handshake (GET) ------------------------------------------------

def test_verification_returns_challenge(client):
    r = client.get("/webhook/facebook", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY,
                                                "hub.challenge": "12345"})
    assert r.status_code == 200 and r.text == "12345"


def test_verification_with_wrong_token_fails(client):
    r = client.get("/webhook/facebook", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                                "hub.challenge": "12345"})
    assert r.status_code == 403


def test_verification_fails_when_token_not_configured(client, monkeypatch, fb_settings):
    monkeypatch.setattr(fb_module, "settings", dataclasses.replace(fb_settings, fb_verify_token=""))
    r = client.get("/webhook/facebook", params={"hub.mode": "subscribe", "hub.verify_token": "",
                                                "hub.challenge": "1"})
    assert r.status_code == 403


# ---- signatures -----------------------------------------------------------

def test_signed_message_creates_ticket_receipt_and_triage(client, session_factory, sent, fake_llm):
    r = post_signed(client, payload(event()))
    assert r.status_code == 200
    with session_factory() as s:
        ticket = s.scalar(select(Ticket))
        assert ticket.channel == "facebook"
        assert ticket.customer.external_user_id == "PSID-1"
        assert [m.direction for m in ticket.messages] == [MessageDirection.INBOUND, MessageDirection.OUTBOUND]
        assert s.scalar(select(TriageResult)).route == "pc_troubleshooting_bot"
    assert len(sent) == 1 and sent[0][0] == "PSID-1"
    assert ticket.ticket_number in sent[0][1]


def test_wrong_signature_rejected(client, session_factory, sent):
    assert post_signed(client, payload(event()), secret="attacker-guess").status_code == 403
    assert count(session_factory, Ticket) == 0 and sent == []


def test_missing_signature_rejected(client, session_factory):
    r = client.post("/webhook/facebook", json=payload(event()))
    assert r.status_code == 403
    assert count(session_factory, Ticket) == 0


def test_all_webhooks_rejected_when_secret_not_configured(client, monkeypatch, fb_settings):
    monkeypatch.setattr(fb_module, "settings", dataclasses.replace(fb_settings, fb_app_secret=""))
    assert post_signed(client, payload(event()), secret="").status_code == 403


def test_tampered_body_rejected(client):
    body = json.dumps(payload(event())).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    tampered = body.replace(b"won't turn on", b"refund me now")
    r = client.post("/webhook/facebook", content=tampered, headers={"X-Hub-Signature-256": signature})
    assert r.status_code == 403


def test_invalid_json_with_valid_signature(client):
    body = b"{not json"
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    r = client.post("/webhook/facebook", content=body, headers={"X-Hub-Signature-256": signature})
    assert r.status_code == 400


# ---- event handling -------------------------------------------------------

def test_echoes_and_read_receipts_are_ignored(client, session_factory, sent):
    read_receipt = {"sender": {"id": "PSID-1"}, "recipient": {"id": "PAGE"}, "read": {"watermark": 1}}
    r = post_signed(client, payload(event(is_echo=True), read_receipt))
    assert r.status_code == 200
    assert count(session_factory, Ticket) == 0 and sent == []


def test_non_page_object_is_ignored(client, session_factory):
    assert post_signed(client, {"object": "instagram", "entry": []}).status_code == 200
    assert count(session_factory, Ticket) == 0


def test_photo_without_text_still_opens_a_ticket(client, session_factory, sent):
    photo = {"sender": {"id": "PSID-2"}, "recipient": {"id": "PAGE"},
             "message": {"mid": "mid.p", "attachments": [{"type": "image", "payload": {"url": "https://x"}}]}}
    post_signed(client, payload(photo))
    with session_factory() as s:
        assert s.scalar(select(Message.text).where(Message.direction == MessageDirection.INBOUND)) \
            == "[Customer sent: image]"


def test_button_postback_becomes_a_message(client, session_factory, sent):
    postback = {"sender": {"id": "PSID-3"}, "recipient": {"id": "PAGE"},
                "postback": {"mid": "mid.b", "title": "Repair status", "payload": "REPAIR_STATUS"}}
    post_signed(client, payload(postback))
    with session_factory() as s:
        assert s.scalar(select(Message.text)) == "Repair status"


def test_batch_from_two_customers_makes_two_tickets(client, session_factory, sent):
    post_signed(client, payload(event("PSID-A", mid="mid.a"), event("PSID-B", mid="mid.b")))
    assert count(session_factory, Ticket) == 2
    assert {user for user, _ in sent} == {"PSID-A", "PSID-B"}


def test_redelivered_webhook_is_processed_once(client, session_factory, sent, fake_llm):
    post_signed(client, payload(event()))
    post_signed(client, payload(event()))   # Meta retries the same delivery
    assert count(session_factory, Ticket) == 1
    assert len(sent) == 1
    assert len(fake_llm.prompts) == 1


def test_follow_up_joins_ticket_without_second_receipt(client, session_factory, sent, fake_llm):
    post_signed(client, payload(event(mid="mid.1")))
    post_signed(client, payload(event(mid="mid.2", text="It's a Dell Inspiron")))
    assert count(session_factory, Ticket) == 1
    assert len(sent) == 1                     # only the first message gets a receipt
    assert len(fake_llm.prompts) == 2         # re-triaged with the whole conversation
    assert "It's a Dell Inspiron" in fake_llm.prompts[-1]


def test_failed_receipt_still_keeps_the_ticket(client, session_factory, monkeypatch):
    def down(user, text):
        raise ChannelError("Facebook is down")
    monkeypatch.setattr(ADAPTERS["facebook"], "send_message", down)
    assert post_signed(client, payload(event())).status_code == 200
    assert count(session_factory, Ticket) == 1


def test_overlong_text_is_trimmed_not_rejected(client, session_factory, sent):
    post_signed(client, payload(event(text="x" * 5000)))
    with session_factory() as s:
        assert len(s.scalar(select(Message.text))) == 2000


# ---- sending through the Graph API ----------------------------------------

def test_send_message_calls_graph_api(fb_settings):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(200, json={"recipient_id": "PSID-1", "message_id": "m"})

    FacebookAdapter(transport=httpx.MockTransport(handler)).send_message("PSID-1", "Hello!")
    [request] = calls
    assert request.url.path == f"/{fb_settings.fb_graph_api_version}/me/messages"
    assert request.headers["Authorization"] == f"Bearer {PAGE_TOKEN}"
    assert PAGE_TOKEN not in str(request.url)     # token never in the URL (URLs end up in logs)
    assert json.loads(request.content) == {"recipient": {"id": "PSID-1"}, "messaging_type": "RESPONSE",
                                           "message": {"text": "Hello!"}}


def test_long_reply_is_sent_in_parts():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content)["message"]["text"])
        return httpx.Response(200, json={})

    FacebookAdapter(transport=httpx.MockTransport(handler)).send_message("PSID-1", "word " * 900)
    assert len(bodies) == 3 and all(len(b) <= 2000 for b in bodies)


def test_graph_api_error_raises_without_leaking_token():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "Invalid OAuth access token."}})

    with pytest.raises(ChannelError) as info:
        FacebookAdapter(transport=httpx.MockTransport(handler)).send_message("PSID-1", "Hi")
    assert "Invalid OAuth access token" in str(info.value)
    assert PAGE_TOKEN not in str(info.value)


def test_network_error_becomes_channel_error():
    def handler(request):
        raise httpx.ConnectError("no route to host")

    with pytest.raises(ChannelError):
        FacebookAdapter(transport=httpx.MockTransport(handler)).send_message("PSID-1", "Hi")


def test_missing_page_token_is_a_clear_error(monkeypatch, fb_settings):
    monkeypatch.setattr(fb_module, "settings", dataclasses.replace(fb_settings, fb_page_access_token=""))
    with pytest.raises(ChannelError, match="FB_PAGE_ACCESS_TOKEN"):
        FacebookAdapter().send_message("PSID-1", "Hi")


def test_split_text():
    assert split_text("short") == ["short"]
    parts = split_text("a" * 4500)                 # no spaces at all (e.g. some Burmese text)
    assert [len(p) for p in parts] == [2000, 2000, 500]
    parts = split_text("line one\n" + "b" * 1995 + " end", limit=2000)
    assert parts[0] == "line one"
