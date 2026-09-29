"""Walk-in kiosk channel: a tablet at the counter where customers type their name and need."""
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.channels.base import ChannelAdapter
from app.config import settings
from app.schemas import IncomingMessage, Language


class WalkInPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    need: str = Field(min_length=1, max_length=settings.max_message_length)
    # Random ID the kiosk page creates for each fresh form. A double-tap or a retry sends the
    # same token, so the customer gets the same ticket instead of two.
    form_token: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    lang: Language | None = None

    @field_validator("name", "need", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class WalkInAdapter(ChannelAdapter):
    name = "walkin"

    def verify_request(self, headers: Mapping[str, str], body: bytes) -> bool:
        # The kiosk is our own page, so there is no platform signature to check.
        # Abuse is limited by rate limiting and input validation in webhooks.py.
        return True

    def parse_incoming(self, headers: Mapping[str, str], body: bytes) -> list[IncomingMessage]:
        payload = WalkInPayload.model_validate_json(body)  # raises ValidationError if invalid
        return [
            IncomingMessage(
                channel=self.name,
                # Walk-ins have no account, so each form submission is its own "customer".
                external_user_id=f"walkin-{payload.form_token}",
                external_message_id=payload.form_token,
                customer_name=payload.name,
                text=payload.need,
                language_hint=payload.lang,
            )
        ]

    def send_message(self, external_user_id: str, text: str) -> None:
        # The receipt is shown on the kiosk screen by the HTTP response, and later replies
        # happen in person at the counter, so there is nothing to push.
        return None
