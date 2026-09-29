import dataclasses
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import admin
from app.channels import ADAPTERS
from app.channels.base import ChannelError
from app.models import Message, MessageDirection, Ticket, TicketStatus, TriageResult
from app.schemas import IncomingMessage
from app.tickets import handle_incoming
from app.triage import run_triage

KEY = "test-admin-key"
AUTH = {"X-Admin-Key": KEY}


@pytest.fixture(autouse=True)
def admin_key(monkeypatch):
    monkeypatch.setattr(admin, "settings", dataclasses.replace(admin.settings, admin_api_key=KEY))


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(ADAPTERS["facebook"], "send_message", lambda user, text: sent.append((user, text)))
    return sent


def make_ticket(session_factory, text="My laptop won't turn on", user="PSID-1", mid=None, channel="facebook",
                triage=True):
    with session_factory() as s:
        incoming = IncomingMessage(channel=channel, external_user_id=user, external_message_id=mid or f"mid-{user}-{text}",
                                   text=text)
        handled = handle_incoming(s, ADAPTERS[channel], incoming)
        if triage:
            run_triage(s, handled.intake.ticket.id)
        return handled.intake.ticket.ticket_number


# ---- access control -------------------------------------------------------

def test_no_key_is_rejected(client):
    assert client.get("/admin/tickets").status_code == 401


def test_wrong_key_is_rejected(client):
    assert client.get("/admin/tickets", headers={"X-Admin-Key": "guess"}).status_code == 401


def test_admin_disabled_without_configured_key(client, monkeypatch):
    monkeypatch.setattr(admin, "settings", dataclasses.replace(admin.settings, admin_api_key=""))
    r = client.get("/admin/tickets", headers={"X-Admin-Key": ""})
    assert r.status_code == 503 and "ADMIN_API_KEY" in r.json()["detail"]


# ---- listing and detail ---------------------------------------------------

def test_list_shows_triage_and_sorts_most_urgent_first(client, session_factory, fake_llm, outbox):
    fake_llm.output = {**fake_llm.DEFAULT_OUTPUT, "issue_type": "price_inquiry", "urgency": "low"}
    make_ticket(session_factory, "How much is an SSD?", user="A")
    fake_llm.output = {**fake_llm.DEFAULT_OUTPUT, "issue_type": "network_internet", "urgency": "critical"}
    critical = make_ticket(session_factory, "Whole office offline", user="B")

    tickets = client.get("/admin/tickets", headers=AUTH).json()
    assert [t["ticket_number"] for t in tickets][0] == critical
    first = tickets[0]
    assert first["route"] == "owner" and first["urgency"] == "critical"
    assert first["waiting_for_us"] is True
    assert first["hours_left_to_reply"] > 23


def test_filter_by_route_and_status(client, session_factory, fake_llm, outbox):
    make_ticket(session_factory, user="A")   # fake LLM: hardware_repair -> pc bot
    tickets = client.get("/admin/tickets", params={"route": "pc_troubleshooting_bot"}, headers=AUTH).json()
    assert len(tickets) == 1
    assert client.get("/admin/tickets", params={"route": "owner"}, headers=AUTH).json() == []
    assert client.get("/admin/tickets", params={"status": "closed"}, headers=AUTH).json() == []


def test_detail_has_conversation_and_draft(client, session_factory, outbox):
    number = make_ticket(session_factory)
    detail = client.get(f"/admin/tickets/{number}", headers=AUTH).json()
    assert detail["draft_reply"].startswith("Sorry")
    assert [m["direction"] for m in detail["messages"]] == ["inbound", "outbound"]   # message + receipt


def test_unknown_ticket_404(client):
    assert client.get("/admin/tickets/RBN-00000000-0000", headers=AUTH).status_code == 404


# ---- replying -------------------------------------------------------------

def test_send_ai_draft_as_is(client, session_factory, outbox):
    number = make_ticket(session_factory)
    outbox.clear()                                         # ignore the receipt
    r = client.post(f"/admin/tickets/{number}/reply", json={}, headers=AUTH)
    assert r.status_code == 200
    assert outbox == [("PSID-1", "Sorry to hear that! Please bring it in today.")]
    body = r.json()
    assert body["status"] == "in_progress" and body["waiting_for_us"] is False
    assert body["messages"][-1]["text"] == outbox[0][1]


def test_send_own_text(client, session_factory, outbox):
    number = make_ticket(session_factory)
    outbox.clear()
    client.post(f"/admin/tickets/{number}/reply", json={"text": "  We open at 9am, come any time!  "}, headers=AUTH)
    assert outbox == [("PSID-1", "We open at 9am, come any time!")]


def test_edit_draft_then_send(client, session_factory, outbox):
    number = make_ticket(session_factory)
    outbox.clear()
    r = client.put(f"/admin/tickets/{number}/draft", json={"text": "Edited reply"}, headers=AUTH)
    assert r.json()["draft_reply"] == "Edited reply" and outbox == []    # saving doesn't send
    client.post(f"/admin/tickets/{number}/reply", json={}, headers=AUTH)
    assert outbox == [("PSID-1", "Edited reply")]


def test_failed_send_returns_502_and_records_nothing(client, session_factory, monkeypatch):
    number = make_ticket(session_factory)
    def down(user, text):
        raise ChannelError("Facebook is down")
    monkeypatch.setattr(ADAPTERS["facebook"], "send_message", down)
    r = client.post(f"/admin/tickets/{number}/reply", json={"text": "Hi"}, headers=AUTH)
    assert r.status_code == 502
    with session_factory() as s:
        texts = s.scalars(select(Message.text).where(Message.direction == MessageDirection.OUTBOUND)).all()
        assert "Hi" not in texts
        assert s.scalar(select(Ticket)).status is not TicketStatus.IN_PROGRESS


def test_reply_blocked_after_messenger_window(client, session_factory, outbox):
    number = make_ticket(session_factory)
    with session_factory() as s:   # pretend the customer wrote 25 hours ago
        msg = s.scalar(select(Message).where(Message.direction == MessageDirection.INBOUND))
        msg.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        s.commit()
    outbox.clear()
    assert client.get(f"/admin/tickets/{number}", headers=AUTH).json()["hours_left_to_reply"] == 0
    r = client.post(f"/admin/tickets/{number}/reply", json={"text": "Hi"}, headers=AUTH)
    assert r.status_code == 409 and "24" in r.json()["detail"]
    assert outbox == []


def test_walkin_has_no_reply_window(client, session_factory):
    number = make_ticket(session_factory, channel="walkin", user="walkin-x")
    detail = client.get(f"/admin/tickets/{number}", headers=AUTH).json()
    assert detail["hours_left_to_reply"] is None
    # "Replying" to a walk-in records what staff told them in person.
    assert client.post(f"/admin/tickets/{number}/reply", json={"text": "Ready at 5pm"}, headers=AUTH).status_code == 200


def test_customer_follow_up_after_reply_is_waiting_again(client, session_factory, outbox):
    number = make_ticket(session_factory, mid="m1")
    client.post(f"/admin/tickets/{number}/reply", json={"text": "What model is it?"}, headers=AUTH)
    make_ticket(session_factory, text="It's a Dell", mid="m2", triage=False)
    detail = client.get(f"/admin/tickets/{number}", headers=AUTH).json()
    assert detail["status"] == "in_progress"         # triage doesn't take it back from staff
    assert detail["waiting_for_us"] is True


# ---- status and retriage --------------------------------------------------

def test_close_ticket_hides_it_from_default_list(client, session_factory, outbox):
    number = make_ticket(session_factory)
    r = client.post(f"/admin/tickets/{number}/status", json={"status": "closed"}, headers=AUTH)
    assert r.json()["status"] == "closed" and r.json()["waiting_for_us"] is False
    assert client.get("/admin/tickets", headers=AUTH).json() == []
    assert len(client.get("/admin/tickets", params={"include_closed": True}, headers=AUTH).json()) == 1


def test_invalid_status_rejected(client, session_factory, outbox):
    number = make_ticket(session_factory)
    assert client.post(f"/admin/tickets/{number}/status", json={"status": "done-ish"}, headers=AUTH).status_code == 422


def test_retriage_fixes_a_failed_ticket(client, session_factory, fake_llm, outbox):
    fake_llm.error = RuntimeError("quota exceeded")
    number = make_ticket(session_factory)
    with session_factory() as s:
        assert s.scalar(select(Ticket)).status is TicketStatus.NEEDS_MANUAL_TRIAGE
    fake_llm.error = None
    assert client.post(f"/admin/tickets/{number}/retriage", headers=AUTH).status_code == 202
    detail = client.get(f"/admin/tickets/{number}", headers=AUTH).json()
    assert detail["status"] == "awaiting_approval" and detail["route"] == "pc_troubleshooting_bot"


def test_prompt_forbids_claiming_things_already_happened():
    from app.triage import SYSTEM_PROMPT
    assert "Never say that anyone has already done something" in SYSTEM_PROMPT
