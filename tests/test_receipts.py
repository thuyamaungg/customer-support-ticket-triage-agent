import pytest

from app.ratelimit import RateLimiter
from app.receipts import build_receipt, detect_language, short_number


@pytest.mark.parametrize("text, expected", [
    ("My laptop won't turn on", "en"),
    ("ကွန်ပျူတာ ဖွင့်မရပါ", "my"),
    ("คอมเปิดไม่ติดครับ", "th"),
    ("Dell laptop ဖွင့်မရဘူး", "my"),   # mixed: Burmese script wins over Latin
    ("1234 ???", "en"),
])
def test_detect_language(text, expected):
    assert detect_language(text) == expected


@pytest.mark.parametrize("channel", ["walkin", "facebook", "some_future_channel"])
@pytest.mark.parametrize("lang", ["my", "th", "en"])
def test_every_receipt_contains_the_ticket_number(channel, lang):
    assert "RBN-20260924-0007" in build_receipt("RBN-20260924-0007", channel, lang)


def test_short_number():
    assert short_number("RBN-20260924-0007") == "0007"


def test_rate_limiter_window():
    now = [0.0]
    limiter = RateLimiter(max_requests=2, window_seconds=60, clock=lambda: now[0])
    assert limiter.allow("ip") and limiter.allow("ip")
    assert not limiter.allow("ip")
    assert limiter.allow("other-ip")       # limits are per key
    now[0] = 61
    assert limiter.allow("ip")             # window has passed
