"""TikTok channel adapter (stub). Fill in the TODOs to enable this channel."""
from collections.abc import Mapping

from app.channels.base import ChannelAdapter
from app.schemas import IncomingMessage


class TikTokAdapter(ChannelAdapter):
    name = "tiktok"

    def verify_request(self, headers: Mapping[str, str], body: bytes) -> bool:
        # TODO: confirm your account has TikTok's business messaging API access (availability varies by region), then follow its signature scheme.
        raise NotImplementedError("TikTok channel is not implemented yet")

    def parse_incoming(self, headers: Mapping[str, str], body: bytes) -> list[IncomingMessage]:
        # TODO: map TikTok's message webhook fields to IncomingMessage.
        raise NotImplementedError("TikTok channel is not implemented yet")

    def send_message(self, external_user_id: str, text: str) -> None:
        # TODO: call TikTok's send-message endpoint.
        raise NotImplementedError("TikTok channel is not implemented yet")
