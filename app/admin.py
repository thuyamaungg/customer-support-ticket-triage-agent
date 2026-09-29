"""Admin endpoints: review tickets, edit AI drafts, send replies, change status.

Every endpoint needs the X-Admin-Key header. Try them in the browser at /docs
(click "Authorize" and paste your ADMIN_API_KEY).
"""
import hmac
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.channels import get_adapter
from app.config import settings
from app.db import get_session, get_session_factory
from app.models import AUTOMATION_OWNED_STATUSES, Message, MessageDirection, Ticket, TicketStatus
from app.triage import run_triage_job

logger = logging.getLogger(__name__)

_api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


def require_admin(key: str | None = Security(_api_key_header)) -> None:
    if not settings.admin_api_key:
        raise HTTPException(status_code=503, detail="Admin API is disabled. Set ADMIN_API_KEY in .env.")
    if not key or not hmac.compare_digest(key, settings.admin_api_key):
        raise HTTPException(status_code=401, detail="Missing or wrong X-Admin-Key.")


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# ---- reply windows --------------------------------------------------------

def reply_window_hours(channel: str) -> float | None:
    """Hours after the customer's last message during which the platform accepts replies.
    None = no limit (walk-in replies are given in person)."""
    return {"facebook": settings.fb_reply_window_hours}.get(channel)


def as_utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes; everything is stored in UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def last_customer_message(ticket: Ticket) -> Message | None:
    inbound = [m for m in ticket.messages if m.direction is MessageDirection.INBOUND]
    return inbound[-1] if inbound else None


def hours_left_to_reply(ticket: Ticket, now: datetime | None = None) -> float | None:
    window = reply_window_hours(ticket.channel)
    last = last_customer_message(ticket)
    if window is None or last is None:
        return None
    deadline = as_utc(last.created_at) + timedelta(hours=window)
    remaining = (deadline - (now or datetime.now(timezone.utc))).total_seconds() / 3600
    return round(max(remaining, 0.0), 1)


# ---- response models ------------------------------------------------------

class TicketSummary(BaseModel):
    ticket_number: str
    channel: str
    customer_name: str | None
    status: TicketStatus
    route: str | None
    issue_type: str | None
    urgency: str | None
    confidence: float | None
    summary: str | None
    waiting_for_us: bool = Field(description="True when the customer spoke last")
    hours_left_to_reply: float | None = Field(description="Platform reply window left; null = no limit")
    updated_at: datetime


class MessageOut(BaseModel):
    direction: MessageDirection
    text: str
    created_at: datetime


class TicketDetail(TicketSummary):
    draft_reply: str | None
    reasoning: str | None
    model_name: str | None
    messages: list[MessageOut]


class ReplyIn(BaseModel):
    text: str | None = Field(default=None, max_length=5000,
                             description="Reply to send. Leave empty to send the AI draft as it is.")


class DraftIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class StatusIn(BaseModel):
    status: TicketStatus


def _summary_fields(ticket: Ticket) -> dict:
    triage = ticket.triage_results[-1] if ticket.triage_results else None
    last = ticket.messages[-1] if ticket.messages else None
    # Waiting for us: nobody has answered yet (the automatic receipt doesn't count, and those
    # tickets are still in an automation status), or the customer wrote again after our reply.
    waiting = ticket.status is not TicketStatus.CLOSED and (
        ticket.status in AUTOMATION_OWNED_STATUSES
        or (last is not None and last.direction is MessageDirection.INBOUND)
    )
    return {
        "ticket_number": ticket.ticket_number,
        "channel": ticket.channel,
        "customer_name": ticket.customer.display_name,
        "status": ticket.status,
        "route": triage.route if triage else None,
        "issue_type": triage.issue_type if triage else None,
        "urgency": triage.urgency if triage else None,
        "confidence": triage.confidence if triage else None,
        "summary": triage.summary if triage else None,
        "waiting_for_us": waiting,
        "hours_left_to_reply": hours_left_to_reply(ticket),
        "updated_at": ticket.updated_at,
    }


def _detail(ticket: Ticket) -> TicketDetail:
    triage = ticket.triage_results[-1] if ticket.triage_results else None
    return TicketDetail(
        **_summary_fields(ticket),
        draft_reply=triage.draft_reply if triage else None,
        reasoning=triage.reasoning if triage else None,
        model_name=triage.model_name if triage else None,
        messages=[MessageOut(direction=m.direction, text=m.text, created_at=m.created_at) for m in ticket.messages],
    )


def _get_ticket(session: Session, ticket_number: str) -> Ticket:
    ticket = session.scalar(select(Ticket).where(Ticket.ticket_number == ticket_number))
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_number} not found.")
    return ticket


# ---- endpoints ------------------------------------------------------------

URGENCY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, None: 4}


@router.get("/tickets", response_model=list[TicketSummary])
def list_tickets(
    status: TicketStatus | None = None,
    route: str | None = None,
    include_closed: bool = False,
    limit: int = 50,
    session: Session = Depends(get_session),
) -> list[TicketSummary]:
    """Open tickets, most urgent first. Filter by status or route."""
    query = select(Ticket).order_by(Ticket.updated_at.desc())
    if status is not None:
        query = query.where(Ticket.status == status)
    elif not include_closed:
        query = query.where(Ticket.status != TicketStatus.CLOSED)
    tickets = [TicketSummary(**_summary_fields(t)) for t in session.scalars(query.limit(min(limit, 200)))]
    if route:
        tickets = [t for t in tickets if t.route == route]
    # Tickets waiting for us first, then by urgency; the database already sorted by recency.
    return sorted(tickets, key=lambda t: (not t.waiting_for_us, URGENCY_ORDER.get(t.urgency, 4)))


@router.get("/tickets/{ticket_number}", response_model=TicketDetail)
def get_ticket(ticket_number: str, session: Session = Depends(get_session)) -> TicketDetail:
    """Full conversation, latest triage, and the AI draft."""
    return _detail(_get_ticket(session, ticket_number))


@router.put("/tickets/{ticket_number}/draft", response_model=TicketDetail)
def edit_draft(ticket_number: str, body: DraftIn, session: Session = Depends(get_session)) -> TicketDetail:
    """Save an edited draft without sending it."""
    ticket = _get_ticket(session, ticket_number)
    if not ticket.triage_results:
        raise HTTPException(status_code=409, detail="This ticket has no AI draft yet. Send a reply with your own text instead.")
    ticket.triage_results[-1].draft_reply = body.text.strip()
    session.commit()
    return _detail(ticket)


@router.post("/tickets/{ticket_number}/reply", response_model=TicketDetail)
async def send_reply(ticket_number: str, body: ReplyIn, session: Session = Depends(get_session)) -> TicketDetail:
    """Send a reply to the customer: your own text, or the AI draft if text is empty."""
    ticket = _get_ticket(session, ticket_number)
    text = (body.text or "").strip()
    if not text:
        if not ticket.triage_results:
            raise HTTPException(status_code=409, detail="No AI draft to send. Provide text.")
        text = ticket.triage_results[-1].draft_reply

    if hours_left_to_reply(ticket) == 0:
        window = reply_window_hours(ticket.channel)
        raise HTTPException(status_code=409, detail=(
            f"The {ticket.channel} reply window ({window:g} hours after the customer's last message) has "
            "closed, so the platform will reject this message. Contact the customer another way, or wait "
            "for them to write again."))

    try:
        await run_in_threadpool(get_adapter(ticket.channel).send_message, ticket.customer.external_user_id, text)
    except Exception as exc:
        logger.exception("Reply to %s failed", ticket.ticket_number)
        raise HTTPException(status_code=502, detail=f"Sending failed: {exc}") from exc

    session.add(Message(ticket=ticket, channel=ticket.channel, direction=MessageDirection.OUTBOUND, text=text))
    ticket.status = TicketStatus.IN_PROGRESS
    session.commit()
    session.refresh(ticket)
    return _detail(ticket)


@router.post("/tickets/{ticket_number}/status", response_model=TicketDetail)
def set_status(ticket_number: str, body: StatusIn, session: Session = Depends(get_session)) -> TicketDetail:
    """Change the status, e.g. to closed when the job is done."""
    ticket = _get_ticket(session, ticket_number)
    ticket.status = body.status
    session.commit()
    return _detail(ticket)


@router.post("/tickets/{ticket_number}/retriage", status_code=202)
def retriage(
    ticket_number: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    session_factory: sessionmaker = Depends(get_session_factory),
) -> dict:
    """Run AI triage again (e.g. after it failed, or after changing the prompt)."""
    ticket = _get_ticket(session, ticket_number)
    if ticket.status is TicketStatus.NEEDS_MANUAL_TRIAGE:
        ticket.status = TicketStatus.NEW  # let triage set the status again
        session.commit()
    background_tasks.add_task(run_triage_job, ticket.id, session_factory)
    return {"status": "triage started", "ticket_number": ticket.ticket_number}
