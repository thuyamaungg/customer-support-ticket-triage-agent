"""Registry of channel adapters. Add a new platform by writing an adapter and listing it here."""
from app.channels.base import ChannelAdapter, ChannelError
from app.channels.facebook import FacebookAdapter
from app.channels.line import LineAdapter
from app.channels.telegram import TelegramAdapter
from app.channels.tiktok import TikTokAdapter
from app.channels.viber import ViberAdapter
from app.channels.walkin import WalkInAdapter

ADAPTERS: dict[str, ChannelAdapter] = {
    adapter.name: adapter
    for adapter in (
        WalkInAdapter(),
        FacebookAdapter(),
        TelegramAdapter(),
        ViberAdapter(),
        LineAdapter(),
        TikTokAdapter(),
    )
}


def get_adapter(name: str) -> ChannelAdapter:
    return ADAPTERS[name]


__all__ = ["ADAPTERS", "ChannelAdapter", "ChannelError", "get_adapter"]
