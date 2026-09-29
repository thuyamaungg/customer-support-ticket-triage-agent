"""Application settings, read from environment variables (.env in development)."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# Hide pydantic-ai's promotional console banner (must be set before the first agent run).
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

# Default model per provider: a fast, low-cost model from each, good for classification.
DEFAULT_TRIAGE_MODELS = {
    "gemini": "google:gemini-flash-latest",  # Google's alias for its newest Flash model
    "openai": "openai:gpt-5.4-mini",
    "claude": "anthropic:claude-haiku-4-5",
}

# Backups tried in order when the main model fails (overloaded, out of quota, retired).
# Free-tier quotas are counted per model, so a different model often still has quota left.
# Providers retire models regularly; if a backup returns 404, replace it here or in .env.
DEFAULT_TRIAGE_FALLBACKS = {
    "gemini": "google:gemini-3.6-flash,google:gemini-3.5-flash-lite,google:gemini-flash-lite-latest",
    "openai": "openai:gpt-4.1-mini",
    "claude": "anthropic:claude-sonnet-4-6",
}


REPLY_POLICIES = ("hold_for_approval", "auto_send_simple")


def _split_models(value: str) -> tuple[str, ...]:
    return tuple(m.strip() for m in value.split(",") if m.strip())


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./triage.db")
    # Shown at the top of the walk-in kiosk page.
    store_name: str = os.getenv("STORE_NAME", "Our Store")
    # Prefix for ticket numbers, e.g. RBN-20260924-0001
    ticket_prefix: str = os.getenv("TICKET_PREFIX", "RBN")
    # The "day" in a ticket number follows the store's local time, not UTC.
    ticket_timezone: str = os.getenv("TICKET_TIMEZONE", "Asia/Yangon")
    max_message_length: int = int(os.getenv("MAX_MESSAGE_LENGTH", "2000"))
    # Requests larger than this are rejected before parsing.
    max_body_bytes: int = int(os.getenv("MAX_BODY_BYTES", "20000"))
    # All walk-ins share one tablet (one IP), so keep this above your busiest minute.
    kiosk_rate_limit_per_minute: int = int(os.getenv("KIOSK_RATE_LIMIT_PER_MINUTE", "10"))
    # Which AI provider triages tickets: gemini, openai, or claude.
    triage_provider: str = field(default_factory=lambda: os.getenv("TRIAGE_PROVIDER", "gemini").strip().lower())
    # Optional: a specific pydantic-ai model string, e.g. "google:gemini-3.6-flash".
    # Leave empty to use the provider's default from DEFAULT_TRIAGE_MODELS.
    triage_model: str = field(default_factory=lambda: os.getenv("TRIAGE_MODEL", "").strip())
    # Comma-separated backup models. Unset = provider default; set but empty = no fallback.
    # Models from other providers are allowed; any whose API key is missing are skipped.
    triage_fallback_models: tuple[str, ...] | None = field(
        default_factory=lambda: None if (v := os.getenv("TRIAGE_FALLBACK_MODELS")) is None else _split_models(v)
    )
    # Below this confidence the label is not trusted and a human reviews the ticket.
    triage_confidence_threshold: float = float(os.getenv("TRIAGE_CONFIDENCE_THRESHOLD", "0.7"))
    triage_timeout_seconds: float = float(os.getenv("TRIAGE_TIMEOUT_SECONDS", "30"))
    # hold_for_approval: every AI draft waits for a human (safe default).
    # auto_send_simple: simple, confident, low/medium-urgency drafts go out automatically.
    # Facebook Messenger (Meta app dashboard). Read when used, so the app runs without them.
    fb_app_secret: str = os.getenv("FB_APP_SECRET", "")
    fb_verify_token: str = os.getenv("FB_VERIFY_TOKEN", "")
    fb_page_access_token: str = os.getenv("FB_PAGE_ACCESS_TOKEN", "")
    fb_graph_api_version: str = os.getenv("FB_GRAPH_API_VERSION", "v26.0")
    fb_max_body_bytes: int = int(os.getenv("FB_MAX_BODY_BYTES", "200000"))
    # Messenger only allows normal replies within this many hours of the customer's last message.
    fb_reply_window_hours: float = float(os.getenv("FB_REPLY_WINDOW_HOURS", "24"))
    # Key for the /admin endpoints (send it in the X-Admin-Key header). Empty = admin disabled.
    admin_api_key: str = os.getenv("ADMIN_API_KEY", "")
    reply_policy: str = field(default_factory=lambda: os.getenv("REPLY_POLICY", "hold_for_approval").strip().lower())


    def __post_init__(self):
        if self.reply_policy not in REPLY_POLICIES:
            raise ValueError(f"REPLY_POLICY must be one of {', '.join(REPLY_POLICIES)}, got {self.reply_policy!r}")
        if self.triage_provider not in DEFAULT_TRIAGE_MODELS:
            raise ValueError(
                f"TRIAGE_PROVIDER must be one of {', '.join(DEFAULT_TRIAGE_MODELS)}, got {self.triage_provider!r}"
            )
        if not self.triage_model:
            # frozen dataclass: set the derived default this way
            object.__setattr__(self, "triage_model", DEFAULT_TRIAGE_MODELS[self.triage_provider])
        if self.triage_fallback_models is None:
            defaults = _split_models(DEFAULT_TRIAGE_FALLBACKS[self.triage_provider])
            object.__setattr__(self, "triage_fallback_models", defaults)
        # Never list the main model as its own backup.
        unique = tuple(dict.fromkeys(m for m in self.triage_fallback_models if m != self.triage_model))
        object.__setattr__(self, "triage_fallback_models", unique)

    @property
    def triage_model_chain(self) -> tuple[str, ...]:
        """Main model first, then backups, in the order they are tried."""
        return (self.triage_model, *self.triage_fallback_models)


settings = Settings()
