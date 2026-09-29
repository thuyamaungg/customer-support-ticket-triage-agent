from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app import routing, specialists
from app.channels.base import ChannelError
from app.models import Message, MessageDirection, TicketStatus, TriageResult
from app.routing import ReplyAction, Route, decide_reply_action, decide_route, route_ticket
from app.schemas import IncomingMessage
from app.tickets import intake_message
from app.triage import run_triage

DAY = datetime(2026, 9, 24, 3, 0, tzinfo=timezone.utc)
AUTO = "auto_send_simple"
HOLD = "hold_for_approval"


# ---- route decision (pure function) ---------------------------------------

@pytest.mark.parametrize("issue_type, urgency, confidence, expected", [
    ("hardware_repair", "high", 0.9, Route.PC_TROUBLESHOOTING_BOT),
    ("network_internet", "medium", 0.9, Route.WISP_MONITORING_ASSISTANT),
    ("price_inquiry", "low", 0.9, Route.GENERAL_QUEUE),
    ("order_status", "medium", 0.9, Route.GENERAL_QUEUE),
    ("other", "low", 0.9, Route.GENERAL_QUEUE),
    ("complaint", "high", 0.9, Route.OWNER),
    ("custom_quote", "low", 0.9, Route.OWNER),
    ("network_internet", "critical", 0.95, Route.OWNER),   # critical always goes to the owner
    ("hardware_repair", "low", 0.5, Route.OWNER),          # low confidence: a human decides
])
def test_decide_route(issue_type, urgency, confidence, expected):
    assert decide_route(issue_type, urgency, confidence) is expected


# ---- reply decision (pure function) ---------------------------------------

def result(**overrides):
    fields = {"issue_type": "price_inquiry", "urgency": "low", "confidence": 0.9}
    fields.update(overrides)
    return TriageResult(detected_language="en", summary="s", draft_reply="r", reasoning="x", **fields)


def test_hold_policy_never_auto_sends():
    assert decide_reply_action(result(), Route.GENERAL_QUEUE, "facebook", HOLD) is ReplyAction.HOLD


def test_simple_case_auto_sends_under_auto_policy():
    assert decide_reply_action(result(), Route.GENERAL_QUEUE, "facebook", AUTO) is ReplyAction.AUTO_SEND


@pytest.mark.parametrize("overrides, route, channel", [
    ({"urgency": "high"}, Route.GENERAL_QUEUE, "facebook"),
    ({"confidence": 0.5}, Route.GENERAL_QUEUE, "facebook"),
    ({"issue_type": "complaint"}, Route.OWNER, "facebook"),
    ({}, Route.OWNER, "facebook"),
    ({}, Route.GENERAL_QUEUE, "walkin"),   # walk-ins are answered in person
])
def test_anything_not_simple_is_held(overrides, route, channel):
    assert decide_reply_action(result(**overrides), route, channel, AUTO) is ReplyAction.HOLD


# ---- route_ticket (applies the decisions) ---------------------------------

class FakeAdapter:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_message(self, external_user_id, text):
        if self.fail:
            raise ChannelError("platform is down")
        self.sent.append((external_user_id, text))


@pytest.fixture
def adapter(monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(routing, "get_adapter", lambda channel: fake)
    return fake


def triaged_ticket(session, channel="facebook", **overrides):
    msg = IncomingMessage(channel=channel, external_user_id="u1", external_message_id="m1", text="How much is an SSD?")
    ticket = intake_message(session, msg, now=DAY).ticket
    triage = result(**overrides)
    triage.ticket = ticket
    session.add(triage)
    session.commit()
    return ticket, triage


def outbound(session):
    return session.scalars(select(Message).where(Message.direction == MessageDirection.OUTBOUND)).all()


def test_auto_send_delivers_and_records_reply(session, adapter):
    ticket, triage = triaged_ticket(session)
    assert route_ticket(session, ticket, triage, policy=AUTO) is ReplyAction.AUTO_SEND
    assert adapter.sent == [("u1", "r")]
    assert [m.text for m in outbound(session)] == ["r"]
    assert ticket.status is TicketStatus.IN_PROGRESS
    assert triage.route == "general_queue"


def test_hold_policy_waits_for_approval(session, adapter):
    ticket, triage = triaged_ticket(session)
    assert route_ticket(session, ticket, triage, policy=HOLD) is ReplyAction.HOLD
    assert adapter.sent == [] and outbound(session) == []
    assert ticket.status is TicketStatus.AWAITING_APPROVAL


def test_failed_send_falls_back_to_approval(session, monkeypatch):
    monkeypatch.setattr(routing, "get_adapter", lambda channel: FakeAdapter(fail=True))
    ticket, triage = triaged_ticket(session)
    assert route_ticket(session, ticket, triage, policy=AUTO) is ReplyAction.HOLD
    assert outbound(session) == []
    assert ticket.status is TicketStatus.AWAITING_APPROVAL


def test_low_confidence_goes_to_owner_for_review(session, adapter):
    ticket, triage = triaged_ticket(session, confidence=0.4)
    route_ticket(session, ticket, triage, policy=AUTO)
    assert triage.route == "owner"
    assert ticket.status is TicketStatus.NEEDS_REVIEW
    assert adapter.sent == []


def test_ticket_staff_are_working_on_is_left_alone(session, adapter):
    ticket, triage = triaged_ticket(session)
    ticket.status = TicketStatus.IN_PROGRESS
    route_ticket(session, ticket, triage, policy=AUTO)
    assert triage.route == "general_queue"          # recorded for staff to see
    assert ticket.status is TicketStatus.IN_PROGRESS
    assert adapter.sent == []


# ---- specialist bots ------------------------------------------------------

class FakeBot:
    name = "pc_troubleshooting_bot"

    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.calls = reply, fail, 0

    def suggest_reply(self, ticket, result):
        self.calls += 1
        if self.fail:
            raise RuntimeError("bot is down")
        return self.reply


def test_specialist_reply_replaces_the_draft(session, adapter, monkeypatch):
    bot = FakeBot(reply="Please hold the power button for 30 seconds, then try again.")
    monkeypatch.setitem(specialists.SPECIALISTS, bot.name, bot)
    ticket, triage = triaged_ticket(session, issue_type="hardware_repair", urgency="medium")
    route_ticket(session, ticket, triage, policy=AUTO)
    assert bot.calls == 1
    assert triage.draft_reply.startswith("Please hold the power button")
    assert adapter.sent[0][1] == triage.draft_reply


def test_broken_specialist_keeps_the_triage_draft(session, adapter, monkeypatch):
    monkeypatch.setitem(specialists.SPECIALISTS, "pc_troubleshooting_bot", FakeBot(fail=True))
    ticket, triage = triaged_ticket(session, issue_type="hardware_repair", urgency="medium")
    route_ticket(session, ticket, triage, policy=HOLD)
    assert triage.draft_reply == "r"
    assert ticket.status is TicketStatus.AWAITING_APPROVAL


def test_stub_specialists_keep_the_draft(session, adapter):
    ticket, triage = triaged_ticket(session, issue_type="network_internet", urgency="medium")
    route_ticket(session, ticket, triage, policy=HOLD)
    assert triage.route == "wisp_monitoring_assistant"
    assert triage.draft_reply == "r"


# ---- end to end: triage then routing --------------------------------------

def test_triage_result_is_routed(session, fake_llm):
    msg = IncomingMessage(channel="facebook", external_user_id="u9", external_message_id="m9", text="Laptop won't start")
    ticket = intake_message(session, msg, now=DAY).ticket
    session.commit()
    saved = run_triage(session, ticket.id)
    assert saved.route == "pc_troubleshooting_bot"    # fake LLM says hardware_repair, high
    assert ticket.status is TicketStatus.AWAITING_APPROVAL
