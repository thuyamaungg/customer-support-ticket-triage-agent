"""LINE channel adapter (stub). Fill in the TODOs to enable this channel."""
from collections.abc import Mapping

from app.channels.base import ChannelAdapter
from app.schemas import IncomingMessage


class LineAdapter(ChannelAdapter):
    name = "line"

    def verify_request(self, headers: Mapping[str, str], body: bytes) -> bool:
        # TODO: check X-Line-Signature (base64 HMAC-SHA256 of the raw body with the channel secret).
        raise NotImplementedError("LINE channel is not implemented yet")

    def parse_incoming(self, headers: Mapping[str, str], body: bytes) -> list[IncomingMessage]:
        # TODO: read events[] of type 'message'; source.userId, message.id, message.text.
        raise NotImplementedError("LINE channel is not implemented yet")

    def send_message(self, external_user_id: str, text: str) -> None:
        # TODO: use the Messaging API push (or reply, with the replyToken) endpoint.
        raise NotImplementedError("LINE channel is not implemented yet")
