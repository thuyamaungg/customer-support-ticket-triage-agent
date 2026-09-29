"""The contract every channel (Facebook, Telegram, walk-in kiosk, ...) implements.

Adapters work on raw headers and body bytes rather than FastAPI's Request object, which
keeps them easy to unit test and lets signature checks see the exact bytes received.
"""
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import ClassVar

from app.schemas import IncomingMessage


class ChannelError(Exception):
    """Raised when a platform API call fails (e.g. sending a reply)."""


class ChannelAdapter(ABC):
    name: ClassVar[str]

    @abstractmethod
    def verify_request(self, headers: Mapping[str, str], body: bytes) -> bool:
        """Return True only if the request really came from this platform."""

    @abstractmethod
    def parse_incoming(self, headers: Mapping[str, str], body: bytes) -> list[IncomingMessage]:
        """Turn a platform payload into normalized messages (one webhook may carry several)."""

    @abstractmethod
    def send_message(self, external_user_id: str, text: str) -> None:
        """Send text to the customer. Raise ChannelError on failure."""
