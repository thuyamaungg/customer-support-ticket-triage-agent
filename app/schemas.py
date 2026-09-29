"""The normalized message model every channel adapter produces."""
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.config import settings

Language = Literal["my", "th", "en"]


class IncomingMessage(BaseModel):
    channel: str = Field(min_length=1, max_length=32)            # "facebook", "walkin", ...
    external_user_id: str = Field(min_length=1, max_length=128)  # sender ID on that platform
    # Platform's own message ID, used to ignore duplicate webhook deliveries.
    external_message_id: str | None = Field(default=None, max_length=128)
    customer_name: str | None = Field(default=None, max_length=120)
    text: str = Field(min_length=1, max_length=settings.max_message_length)
    # Set when the channel already knows the customer's language (e.g. the kiosk's language
    # buttons). Otherwise the receipt language is detected from the text.
    language_hint: Language | None = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_payload: dict[str, Any] | None = None

    @field_validator("channel", "external_user_id", "customer_name", "text", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("customer_name")
    @classmethod
    def _blank_name_is_none(cls, value: str | None) -> str | None:
        return value or None
