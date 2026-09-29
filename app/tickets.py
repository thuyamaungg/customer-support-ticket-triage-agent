"""Ticket intake: save the message, find or create the ticket, generate ticket numbers.

The caller owns the transaction and must commit after intake_message() returns.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import OPEN_STATUSES, Customer, Message, MessageDirection, Ticket, TicketStatus, utcnow
from app.receipts import build_receipt, detect_language
from app.schemas import IncomingMessage

if TYPE_CHECKING:
    from app.channels.base import ChannelAdapter

logger = logging.getLogger(__name__)

# One atomic statement: create today's counter at 1, or add 1 to it, and return the new value.
# Two requests can never get the same number, because the database serializes this row update.
# Works on SQLite (3.35+) and PostgreSQL.
_NEXT_COUNTER_SQL = text(
    """
    INSERT INTO ticket_counters (day, last_value) VALUES (:day, 1)
    ON CONFLICT (day) DO UPDATE SET last_value = ticket_counters.last_value + 1
    RETURNING last_value
    """
)


@dataclass
class IntakeResult:
    ticket: Ticket
    message: Message
    is_new_ticket: bool  # False when appended to an existing open ticket
    is_duplicate: bool   # True when this exact platform message was already saved


def local_day(now: datetime | None = None, tz_name: str | None = None) -> str:
    """Return the store-local date as YYYYMMDD."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(ZoneInfo(tz_name or settings.ticket_timezone)).strftime("%Y%m%d")


def next_ticket_number(session: Session, now: datetime | None = None, prefix: str | None = None) -> str:
    """Generate the next ticket number, e.g. RBN-20260924-0001 (the counter resets daily)."""
    day = local_day(now)
    value = session.execute(_NEXT_COUNTER_SQL, {"day": day}).scalar_one()
    return f"{prefix or settings.ticket_prefix}-{day}-{value:04d}"


def _find_duplicate(session: Session, incoming: IncomingMessage) -> Message | None:
    if not incoming.external_message_id:
        return None
    return session.scalar(
        select(Message).where(
            Message.channel == incoming.channel,
            Message.external_message_id == incoming.external_message_id,
        )
    )


def _get_or_create_customer(session: Session, incoming: IncomingMessage) -> Customer:
    customer = session.scalar(
        select(Customer).where(
            Customer.channel == incoming.channel,
            Customer.external_user_id == incoming.external_user_id,
        )
    )
    if customer is None:
        customer = Customer(
            channel=incoming.channel,
            external_user_id=incoming.external_user_id,
            display_name=incoming.customer_name,
        )
        session.add(customer)
    elif incoming.customer_name and not customer.display_name:
        customer.display_name = incoming.customer_name
    return customer


def _find_open_ticket(session: Session, customer: Customer) -> Ticket | None:
    if customer.id is None:  # brand-new customer, cannot have tickets yet
        return None
    return session.scalar(
        select(Ticket)
        .where(Ticket.customer_id == customer.id, Ticket.status.in_(OPEN_STATUSES))
        .order_by(Ticket.created_at.desc())
        .limit(1)
    )


def _intake_once(session: Session, incoming: IncomingMessage, now: datetime | None) -> IntakeResult:
    duplicate = _find_duplicate(session, incoming)
    if duplicate is not None:
        return IntakeResult(duplicate.ticket, duplicate, is_new_ticket=False, is_duplicate=True)

    customer = _get_or_create_customer(session, incoming)
    ticket = _find_open_ticket(session, customer)
    is_new_ticket = ticket is None

    if is_new_ticket:
        ticket = Ticket(
            ticket_number=next_ticket_number(session, now=now),
            customer=customer,
            channel=incoming.channel,
            status=TicketStatus.NEW,
        )
        session.add(ticket)
    else:
        ticket.updated_at = utcnow()

    message = Message(
        ticket=ticket,
        channel=incoming.channel,
        external_message_id=incoming.external_message_id,
        direction=MessageDirection.INBOUND,
        text=incoming.text,
        raw_payload=incoming.raw_payload,
    )
    session.add(message)
    session.flush()  # surfaces unique-constraint errors here, inside our try block
    return IntakeResult(ticket, message, is_new_ticket=is_new_ticket, is_duplicate=False)


def intake_message(session: Session, incoming: IncomingMessage, now: datetime | None = None) -> IntakeResult:
    """Save an incoming message and attach it to a ticket.

    - Duplicate delivery of the same platform message -> returns the existing record.
    - Customer already has an open ticket on this channel -> message is appended to it.
    - Otherwise -> a new ticket with a fresh ticket number.

    On a race (e.g. PostgreSQL, two deliveries at the same instant) the unique constraints
    reject the loser; we roll back and try once more, which then finds the winner's rows.
    """
    try:
        return _intake_once(session, incoming, now)
    except IntegrityError:
        session.rollback()
        return _intake_once(session, incoming, now)


# ---------------------------------------------------------------------------
# Step 2: receipts
# ---------------------------------------------------------------------------

@dataclass
class HandledMessage:
    intake: IntakeResult
    language: str
    receipt_text: str
    receipt_sent: bool             # True only when a receipt went out for this message
    receipt_pending: bool = False  # True when the caller must deliver the receipt itself


def record_outbound(session: Session, ticket: Ticket, text_: str) -> Message:
    message = Message(ticket=ticket, channel=ticket.channel, direction=MessageDirection.OUTBOUND, text=text_)
    session.add(message)
    return message


def deliver_receipt(session: Session, adapter: "ChannelAdapter", ticket: Ticket,
                    external_user_id: str, receipt: str) -> bool:
    """Record the receipt, then send it. A failed send is logged, never raised."""
    record_outbound(session, ticket, receipt)
    session.commit()
    try:
        adapter.send_message(external_user_id, receipt)
    except Exception:  # a failed receipt must never break intake
        logger.exception("Receipt for %s could not be sent via %s", ticket.ticket_number, adapter.name)
        return False
    return True


def handle_incoming(session: Session, adapter: "ChannelAdapter", incoming: IncomingMessage,
                    send_receipt: bool = True) -> HandledMessage:
    """Full intake for one message: save it, then send a receipt if it opened a new ticket.

    - The ticket is committed BEFORE the receipt is sent, so a platform outage can never
      lose a ticket.
    - Follow-ups on an open ticket and duplicate deliveries get no second receipt
      (nobody wants "your ticket number is..." after every message).
    - The receipt text is still returned in every case, so the kiosk can redisplay it.
    - send_receipt=False saves the message only and leaves the receipt to the caller
      (receipt_pending=True), so a webhook can answer the platform before calling its API.
    """
    result = intake_message(session, incoming)
    session.commit()

    language = incoming.language_hint or detect_language(incoming.text)
    receipt = build_receipt(result.ticket.ticket_number, incoming.channel, language)

    if result.is_duplicate or not result.is_new_ticket:
        return HandledMessage(result, language, receipt, receipt_sent=False)
    if not send_receipt:
        return HandledMessage(result, language, receipt, receipt_sent=False, receipt_pending=True)

    sent = deliver_receipt(session, adapter, result.ticket, incoming.external_user_id, receipt)
    return HandledMessage(result, language, receipt, receipt_sent=sent)
