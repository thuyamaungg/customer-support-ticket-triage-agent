"""SQLAlchemy tables: customers, tickets, messages, triage results, and the ticket counter."""
import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum(cls: type[enum.Enum]) -> Enum:
    # Store readable values ("needs_review") as plain strings: portable across SQLite/PostgreSQL.
    return Enum(cls, native_enum=False, length=32, values_callable=lambda e: [m.value for m in e])


class TicketStatus(str, enum.Enum):
    NEW = "new"                                  # just created, triage not finished yet
    NEEDS_REVIEW = "needs_review"                # LLM confidence too low to trust
    NEEDS_MANUAL_TRIAGE = "needs_manual_triage"  # LLM call failed
    AWAITING_APPROVAL = "awaiting_approval"      # draft reply waiting for a human
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


OPEN_STATUSES = tuple(s for s in TicketStatus if s is not TicketStatus.CLOSED)

# Automation (triage, routing) may only move tickets between these statuses. Once staff are
# working on a ticket (in_progress) or it is closed, a new message must not reset it.
AUTOMATION_OWNED_STATUSES = frozenset({
    TicketStatus.NEW,
    TicketStatus.NEEDS_REVIEW,
    TicketStatus.NEEDS_MANUAL_TRIAGE,
    TicketStatus.AWAITING_APPROVAL,
})


class MessageDirection(str, enum.Enum):
    INBOUND = "inbound"    # from the customer
    OUTBOUND = "outbound"  # from the store (receipts, replies)


class Customer(Base):
    __tablename__ = "customers"
    # The same person on Facebook and on Telegram counts as two customers for now.
    __table_args__ = (UniqueConstraint("channel", "external_user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(32))
    external_user_id: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tickets: Mapped[list["Ticket"]] = relationship(back_populates="customer")


class TicketCounter(Base):
    """One row per local day; last_value is the most recent ticket sequence number issued."""

    __tablename__ = "ticket_counters"

    day: Mapped[str] = mapped_column(String(8), primary_key=True)  # "YYYYMMDD"
    last_value: Mapped[int] = mapped_column(default=0)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    status: Mapped[TicketStatus] = mapped_column(_enum(TicketStatus), default=TicketStatus.NEW, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    customer: Mapped[Customer] = relationship(back_populates="tickets")
    messages: Mapped[list["Message"]] = relationship(back_populates="ticket", order_by="Message.id")
    triage_results: Mapped[list["TriageResult"]] = relationship(back_populates="ticket", order_by="TriageResult.id")


class Message(Base):
    __tablename__ = "messages"
    # Platforms may deliver the same webhook twice; this constraint makes duplicates impossible.
    __table_args__ = (UniqueConstraint("channel", "external_message_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    external_message_id: Mapped[str | None] = mapped_column(String(128))
    direction: Mapped[MessageDirection] = mapped_column(_enum(MessageDirection))
    text: Mapped[str] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="messages")


class TriageResult(Base):
    """One LLM classification of a ticket. Filled in by triage.py (step 3)."""

    __tablename__ = "triage_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    issue_type: Mapped[str] = mapped_column(String(32))
    urgency: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    detected_language: Mapped[str] = mapped_column(String(8))
    summary: Mapped[str] = mapped_column(Text)
    draft_reply: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="triage_results")
