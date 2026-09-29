"""Facebook Messenger channel.

Incoming: Meta calls POST /webhook/facebook with a JSON batch of events, signed with our
app secret (X-Hub-Signature-256). Setup: Meta first calls GET /webhook/facebook with our
verify token and expects the "challenge" value back.
Outgoing: POST to the Graph API Send API with the page access token.
"""
import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from typing import Any

import httpx

from app.channels.base import ChannelAdapter, ChannelError
from app.config import settings
from app.schemas import IncomingMessage

logger = logging.getLogger(__name__)

SEND_API_URL = "https://graph.facebook.com/{version}/me/messages"
MAX_REPLY_CHARS = 2000  # Messenger's limit per text message


def split_text(text: str, limit: int = MAX_REPLY_CHARS) -> list[str]:
    """Split long text into chunks under the limit, preferring line and word breaks."""
    chunks, rest = [], text.strip()
    while len(rest) > limit:
        cut = max(rest.rfind("\n", 0, limit), rest.rfind(" ", 0, limit))
        if cut <= 0:
            cut = limit  # one very long word (or a script without spaces): hard cut
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


class FacebookAdapter(ChannelAdapter):
    name = "facebook"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._transport = transport  # tests pass a mock transport; None = real network

    # ---- webhook setup (GET) ----------------------------------------------

    def verify_subscription(self, mode: str | None, token: str | None) -> bool:
        expected = settings.fb_verify_token
        if not expected:
            logger.error("FB_VERIFY_TOKEN is not set; refusing webhook verification")
            return False
        return mode == "subscribe" and bool(token) and hmac.compare_digest(token, expected)

    # ---- incoming (POST) --------------------------------------------------

    def verify_request(self, headers: Mapping[str, str], body: bytes) -> bool:
        secret = settings.fb_app_secret
        if not secret:
            logger.error("FB_APP_SECRET is not set; rejecting all Facebook webhooks")
            return False
        signature = headers.get("x-hub-signature-256", "")
        if not signature.startswith("sha256="):
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        # compare_digest takes the same time for any mismatch, so the secret can't be guessed
        return hmac.compare_digest(signature.removeprefix("sha256="), expected)

    def parse_incoming(self, headers: Mapping[str, str], body: bytes) -> list[IncomingMessage]:
        data = json.loads(body)  # raises ValueError on invalid JSON
        if not isinstance(data, dict) or data.get("object") != "page":
            return []
        messages = []
        for entry in data.get("entry") or []:
            for event in entry.get("messaging") or []:
                message = self._to_message(event)
                if message is not None:
                    messages.append(message)
        return messages

    @staticmethod
    def _to_message(event: dict[str, Any]) -> IncomingMessage | None:
        sender = (event.get("sender") or {}).get("id")
        if not sender:
            return None

        if "message" in event:
            msg = event["message"] or {}
            if msg.get("is_echo"):
                return None  # a message our own page sent; not from a customer
            text = (msg.get("text") or "").strip()
            if not text and msg.get("attachments"):
                # Photos, voice notes, stickers: still open a ticket so nothing is missed.
                kinds = sorted({a.get("type", "file") for a in msg["attachments"]})
                text = f"[Customer sent: {', '.join(kinds)}]"
            message_id = msg.get("mid")
        elif "postback" in event:  # the customer tapped a button
            postback = event["postback"] or {}
            text = (postback.get("title") or postback.get("payload") or "").strip()
            message_id = postback.get("mid")
        else:
            return None  # delivery/read receipts, reactions, etc.

        if not text:
            return None
        return IncomingMessage(
            channel="facebook",
            external_user_id=str(sender),
            external_message_id=message_id,
            text=text[: settings.max_message_length],
            raw_payload=event,
        )

    # ---- outgoing ---------------------------------------------------------

    def send_message(self, external_user_id: str, text: str) -> None:
        token = settings.fb_page_access_token
        if not token:
            raise ChannelError("FB_PAGE_ACCESS_TOKEN is not set")
        url = SEND_API_URL.format(version=settings.fb_graph_api_version)
        headers = {"Authorization": f"Bearer {token}"}  # header, not URL, so logs never show the token
        try:
            with httpx.Client(timeout=10, transport=self._transport) as client:
                for chunk in split_text(text):
                    response = client.post(url, headers=headers, json={
                        "recipient": {"id": external_user_id},
                        "messaging_type": "RESPONSE",
                        "message": {"text": chunk},
                    })
                    if response.status_code != 200:
                        raise ChannelError(f"Facebook send failed ({response.status_code}): {_error_text(response)}")
        except httpx.HTTPError as exc:
            raise ChannelError(f"Facebook send failed: {type(exc).__name__}") from exc


def _error_text(response: httpx.Response) -> str:
    try:
        return response.json()["error"]["message"]
    except Exception:
        return response.text[:200]
