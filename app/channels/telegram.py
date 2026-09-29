"""Telegram channel adapter (stub). Fill in the TODOs to enable this channel."""
from collections.abc import Mapping

from app.channels.base import ChannelAdapter
from app.schemas import IncomingMessage


class TelegramAdapter(ChannelAdapter):
    name = "telegram"

    def verify_request(self, headers: Mapping[str, str], body: bytes) -> bool:
        # TODO: compare the X-Telegram-Bot-Api-Secret-Token header with the secret_token set in setWebhook.
        raise NotImplementedError("Telegram channel is not implemented yet")

    def parse_incoming(self, headers: Mapping[str, str], body: bytes) -> list[IncomingMessage]:
        # TODO: read message.from.id, message.message_id, message.text, message.from.first_name from the Update.
        raise NotImplementedError("Telegram channel is not implemented yet")

    def send_message(self, external_user_id: str, text: str) -> None:
        # TODO: call the Bot API sendMessage method with chat_id and text.
        raise NotImplementedError("Telegram channel is not implemented yet")
