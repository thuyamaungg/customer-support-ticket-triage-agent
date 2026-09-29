"""Viber channel adapter (stub). Fill in the TODOs to enable this channel."""
from collections.abc import Mapping

from app.channels.base import ChannelAdapter
from app.schemas import IncomingMessage


class ViberAdapter(ChannelAdapter):
    name = "viber"

    def verify_request(self, headers: Mapping[str, str], body: bytes) -> bool:
        # TODO: check X-Viber-Content-Signature (HMAC-SHA256 hex of the raw body with the bot auth token).
        raise NotImplementedError("Viber channel is not implemented yet")

    def parse_incoming(self, headers: Mapping[str, str], body: bytes) -> list[IncomingMessage]:
        # TODO: handle event == 'message'; use sender.id, message_token, message.text, sender.name.
        raise NotImplementedError("Viber channel is not implemented yet")

    def send_message(self, external_user_id: str, text: str) -> None:
        # TODO: POST to the Viber chat API send_message endpoint with the X-Viber-Auth-Token header.
        raise NotImplementedError("Viber channel is not implemented yet")
