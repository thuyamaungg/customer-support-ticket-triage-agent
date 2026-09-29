"""Specialist bots that routed tickets are handed to.

These are placeholders for two separate projects:
- pc_troubleshooting_bot: a Burmese PC troubleshooting chatbot
- wisp_monitoring_assistant: a network / WISP monitoring assistant

Each bot may return a better, domain-specific reply to replace the triage draft, or None
to keep the triage draft. Wiring a real bot in means replacing suggest_reply's body with a
call to that project's API; nothing else in this project needs to change.
"""
import logging
from typing import Protocol

from app.models import MessageDirection, Ticket, TriageResult

logger = logging.getLogger(__name__)


class SpecialistBot(Protocol):
    name: str

    def suggest_reply(self, ticket: Ticket, result: TriageResult) -> str | None:
        """Return a reply for the customer, or None to keep the triage draft."""


def customer_messages(ticket: Ticket) -> list[str]:
    return [m.text for m in ticket.messages if m.direction is MessageDirection.INBOUND]


class PCTroubleshootingBot:
    name = "pc_troubleshooting_bot"

    def suggest_reply(self, ticket: Ticket, result: TriageResult) -> str | None:
        # TODO: POST {"messages": customer_messages(ticket), "language": result.detected_language}
        # to the PC troubleshooting bot's API and return its first troubleshooting step.
        logger.info("[stub] %s would handle %s", self.name, ticket.ticket_number)
        return None


class WispMonitoringAssistant:
    name = "wisp_monitoring_assistant"

    def suggest_reply(self, ticket: Ticket, result: TriageResult) -> str | None:
        # TODO: ask the WISP assistant whether there is a known outage or signal problem for
        # this customer, and return a reply based on the live network status.
        logger.info("[stub] %s would handle %s", self.name, ticket.ticket_number)
        return None


SPECIALISTS: dict[str, SpecialistBot] = {bot.name: bot for bot in (PCTroubleshootingBot(), WispMonitoringAssistant())}
