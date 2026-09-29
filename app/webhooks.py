"""HTTP entry points for incoming messages, one route per channel."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.channels import get_adapter
from app.config import settings
from app.db import get_session, get_session_factory
from app.ratelimit import RateLimiter
from app.receipts import short_number
from app.models import Ticket
from app.tickets import HandledMessage, deliver_receipt, handle_incoming
from app.triage import run_triage_job

router = APIRouter()

kiosk_limiter = RateLimiter(settings.kiosk_rate_limit_per_minute, window_seconds=60)


async def _read_body(request: Request, limit: int | None = None) -> bytes:
    body = await request.body()
    if len(body) > (limit or settings.max_body_bytes):
        raise HTTPException(status_code=413, detail="Request is too large.")
    return body


def schedule_triage(tasks: BackgroundTasks, handled: HandledMessage, session_factory: sessionmaker) -> None:
    """Triage runs after the response is sent, so a slow LLM never delays the customer.
    New tickets and follow-ups are (re)triaged with the whole conversation; duplicates are not."""
    if not handled.intake.is_duplicate:
        tasks.add_task(run_triage_job, handled.intake.ticket.id, session_factory)


@router.post("/webhook/walkin")
async def walkin_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    session_factory: sessionmaker = Depends(get_session_factory),
) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    if not kiosk_limiter.allow(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests. Wait a minute and try again.")

    body = await _read_body(request)
    adapter = get_adapter("walkin")
    try:
        [incoming] = adapter.parse_incoming(request.headers, body)
    except ValidationError:
        # Keep the message generic: never echo validation internals back to the page.
        raise HTTPException(status_code=422, detail="Enter your name and what you need.")

    # Database work is synchronous; run it off the event loop.
    handled = await run_in_threadpool(handle_incoming, session, adapter, incoming)
    schedule_triage(background_tasks, handled, session_factory)
    number = handled.intake.ticket.ticket_number
    return {
        "ticket_number": number,
        "short_number": short_number(number),
        "receipt": handled.receipt_text,
        "language": handled.language,
    }


# ---------------------------------------------------------------------------
# Facebook Messenger
# ---------------------------------------------------------------------------

@router.get("/webhook/facebook", response_class=PlainTextResponse)
def facebook_verify(
    mode: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
) -> str:
    """One-time setup handshake: Meta checks we know the verify token, then saves the URL."""
    if not get_adapter("facebook").verify_subscription(mode, token):
        raise HTTPException(status_code=403, detail="Verification failed.")
    return challenge


def deliver_receipt_job(ticket_id: int, channel: str, external_user_id: str, receipt: str,
                        session_factory: sessionmaker) -> None:
    with session_factory() as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is not None:
            deliver_receipt(session, get_adapter(channel), ticket, external_user_id, receipt)


@router.post("/webhook/facebook")
async def facebook_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    session_factory: sessionmaker = Depends(get_session_factory),
) -> dict:
    body = await _read_body(request, settings.fb_max_body_bytes)
    adapter = get_adapter("facebook")
    if not adapter.verify_request(request.headers, body):
        raise HTTPException(status_code=403, detail="Invalid signature.")
    try:
        messages = adapter.parse_incoming(request.headers, body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON.")

    # Save every message now (fast, local). Receipts (a Graph API call) and triage (an LLM call)
    # run after we answer, because Meta retries webhooks that respond slowly.
    for incoming in messages:
        handled = await run_in_threadpool(handle_incoming, session, adapter, incoming, False)
        if handled.receipt_pending:
            background_tasks.add_task(deliver_receipt_job, handled.intake.ticket.id, adapter.name,
                                      incoming.external_user_id, handled.receipt_text, session_factory)
        schedule_triage(background_tasks, handled, session_factory)
    return {"status": "ok"}
