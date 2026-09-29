"""Routing: after triage, decide who handles the ticket and whether the draft reply goes out.

The two decisions are plain functions of the triage result, so they are easy to read and test.
route_ticket() then applies them: specialist hand-off, ticket status, and optional auto-send.
"""
import enum
import logging

from sqlalchemy.orm import Session

from app.channels import get_adapter
from app.config import settings
from app.models import AUTOMATION_OWNED_STATUSES, Message, MessageDirection, Ticket, TicketStatus, TriageResult

logger = logging.getLogger(__name__)


class Route(str, enum.Enum):
    PC_TROUBLESHOOTING_BOT = "pc_troubleshooting_bot"
    WISP_MONITORING_ASSISTANT = "wisp_monitoring_assistant"
    OWNER = "owner"                  # needs the shop owner personally
    GENERAL_QUEUE = "general_queue"  # any staff member can answer


class ReplyAction(str, enum.Enum):
    AUTO_SEND = "auto_send"
    HOLD = "hold"


OWNER_ISSUE_TYPES = {"complaint", "custom_quote"}
AUTO_SEND_URGENCIES = {"low", "medium"}
# Walk-in customers are answered in person, so nothing is ever sent to them automatically.
NO_AUTO_SEND_CHANNELS = {"walkin"}


def decide_route(issue_type: str, urgency: str, confidence: float) -> Route:
    """Rules are checked in order; the first match wins."""
    if confidence < settings.triage_confidence_threshold:
        return Route.OWNER                      # the AI isn't sure: a human decides
    if issue_type in OWNER_ISSUE_TYPES or urgency == "critical":
        return Route.OWNER
    if issue_type == "hardware_repair":
        return Route.PC_TROUBLESHOOTING_BOT
    if issue_type == "network_internet":
        return Route.WISP_MONITORING_ASSISTANT
    return Route.GENERAL_QUEUE


def decide_reply_action(result: TriageResult, route: Route, channel: str, policy: str | None = None) -> ReplyAction:
    """Auto-send only simple cases, and only when the policy allows it."""
    policy = policy or settings.reply_policy
    simple = (
        route is not Route.OWNER
        and result.urgency in AUTO_SEND_URGENCIES
        and result.confidence >= settings.triage_confidence_threshold
        and result.issue_type != "complaint"
        and channel not in NO_AUTO_SEND_CHANNELS
    )
    return ReplyAction.AUTO_SEND if policy == "auto_send_simple" and simple else ReplyAction.HOLD


def _ask_specialist(ticket: Ticket, result: TriageResult, route: Route) -> None:
    """Let the specialist bot replace the draft with a better reply, if it has one."""
    from app.specialists import SPECIALISTS  # imported here so tests can swap bots easily

    bot = SPECIALISTS.get(route.value)
    if bot is None:
        return
    try:
        reply = bot.suggest_reply(ticket, result)
    except Exception:  # a broken specialist must never block the ticket
        logger.exception("Specialist %s failed on %s; keeping the triage draft", route.value, ticket.ticket_number)
        return
    if reply and reply.strip():
        result.draft_reply = reply.strip()
        result.reasoning = f"{result.reasoning} (Reply drafted by {route.value}.)"


def route_ticket(session: Session, ticket: Ticket, result: TriageResult, policy: str | None = None) -> ReplyAction:
    """Record the route, update the status, and send the draft if the policy allows."""
    route = decide_route(result.issue_type, result.urgency, result.confidence)
    result.route = route.value

    if ticket.status not in AUTOMATION_OWNED_STATUSES:
        # Staff are already on it: keep the new triage result for them to read, change nothing else.
        session.commit()
        return ReplyAction.HOLD

    _ask_specialist(ticket, result, route)
    action = decide_reply_action(result, route, ticket.channel, policy)
    trusted = result.confidence >= settings.triage_confidence_threshold
    ticket.status = TicketStatus.AWAITING_APPROVAL if trusted else TicketStatus.NEEDS_REVIEW
    session.commit()  # commit before any network call, so a slow platform never holds the DB

    if action is ReplyAction.HOLD:
        logger.info("%s -> %s, reply held for approval", ticket.ticket_number, route.value)
        return action

    try:
        get_adapter(ticket.channel).send_message(ticket.customer.external_user_id, result.draft_reply)
    except Exception:
        logger.exception("Auto-send failed for %s; holding the reply for approval", ticket.ticket_number)
        return ReplyAction.HOLD

    session.add(Message(ticket=ticket, channel=ticket.channel, direction=MessageDirection.OUTBOUND,
                        text=result.draft_reply))
    ticket.status = TicketStatus.IN_PROGRESS
    session.commit()
    logger.info("%s -> %s, reply sent automatically", ticket.ticket_number, route.value)
    return action
