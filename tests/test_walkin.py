import re

from sqlalchemy import select

from app.channels.base import ChannelAdapter, ChannelError
from app.models import Message, MessageDirection, Ticket
from app.schemas import IncomingMessage
from app.tickets import handle_incoming
from app.webhooks import kiosk_limiter

TICKET_RE = re.compile(r"^RBN-\d{8}-\d{4}$")


def post(client, **overrides):
    payload = {"name": "Ma Hla", "need": "Laptop screen is broken", "form_token": "tok-00000001"}
    payload.update(overrides)
    return client.post("/webhook/walkin", json=payload)


# ---- kiosk page -----------------------------------------------------------

def test_kiosk_page_is_served(client):
    r = client.get("/kiosk")
    assert r.status_code == 200
    assert 'id="ticket-form"' in r.text
    assert "{{STORE_NAME}}" not in r.text


def test_root_redirects_to_kiosk(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/kiosk"


# ---- walk-in intake -------------------------------------------------------

def test_walkin_creates_ticket_and_receipt(client, session_factory):
    r = post(client, lang="en")
    assert r.status_code == 200
    data = r.json()
    assert TICKET_RE.match(data["ticket_number"])
    assert data["short_number"] == data["ticket_number"][-4:]
    assert data["ticket_number"] in data["receipt"]
    assert data["language"] == "en"

    with session_factory() as s:
        ticket = s.scalar(select(Ticket))
        assert ticket.customer.display_name == "Ma Hla"
        directions = [m.direction for m in ticket.messages]
        assert directions == [MessageDirection.INBOUND, MessageDirection.OUTBOUND]
        assert ticket.messages[1].text == data["receipt"]


def test_language_detected_when_no_hint(client):
    data = post(client, need="လက်ပ်တော့ ဖွင့်မရဘူး").json()
    assert data["language"] == "my"


def test_kiosk_language_button_wins_over_detection(client):
    data = post(client, need="Laptop screen is broken", lang="th").json()
    assert data["language"] == "th"


def test_double_tap_returns_same_ticket(client, session_factory):
    first = post(client).json()
    second = post(client).json()
    assert first["ticket_number"] == second["ticket_number"]
    with session_factory() as s:
        assert len(s.scalars(select(Message)).all()) == 2   # one inbound + one receipt


def test_each_new_form_gets_its_own_ticket(client):
    a = post(client, form_token="tok-aaaaaaaa").json()
    b = post(client, form_token="tok-bbbbbbbb").json()
    assert a["ticket_number"] != b["ticket_number"]


# ---- validation and limits ------------------------------------------------

def test_missing_fields_rejected(client):
    assert post(client, name="   ").status_code == 422
    assert post(client, need="").status_code == 422


def test_malformed_json_rejected(client):
    r = client.post("/webhook/walkin", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_suspicious_token_rejected(client):
    assert post(client, form_token="<script>alert(1)</script>").status_code == 422


def test_overlong_need_rejected(client):
    assert post(client, need="x" * 2001).status_code == 422


def test_huge_body_rejected(client):
    r = client.post("/webhook/walkin", content=b"x" * 50_000, headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_rate_limit(client):
    for i in range(kiosk_limiter.max_requests):
        assert post(client, form_token=f"tok-{i:08d}").status_code == 200
    assert post(client, form_token="tok-overflow").status_code == 429


# ---- receipt behaviour in handle_incoming ---------------------------------

class RecordingAdapter(ChannelAdapter):
    name = "facebook"

    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def verify_request(self, headers, body):
        return True

    def parse_incoming(self, headers, body):
        return []

    def send_message(self, external_user_id, text):
        if self.fail:
            raise ChannelError("platform is down")
        self.sent.append((external_user_id, text))


def incoming(mid, text="My laptop won't turn on"):
    return IncomingMessage(channel="facebook", external_user_id="u1", external_message_id=mid, text=text)


def test_receipt_sent_once_per_ticket(session):
    adapter = RecordingAdapter()
    first = handle_incoming(session, adapter, incoming("m1"))
    follow_up = handle_incoming(session, adapter, incoming("m2", "Also the fan is loud"))
    duplicate = handle_incoming(session, adapter, incoming("m1"))
    assert first.receipt_sent and not follow_up.receipt_sent and not duplicate.receipt_sent
    assert len(adapter.sent) == 1
    assert adapter.sent[0][0] == "u1"


def test_failed_receipt_does_not_lose_the_ticket(session):
    result = handle_incoming(session, RecordingAdapter(fail=True), incoming("m1"))
    assert result.receipt_sent is False
    assert session.scalar(select(Ticket)).ticket_number == result.intake.ticket.ticket_number
