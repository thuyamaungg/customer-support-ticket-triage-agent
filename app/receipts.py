"""Receipt confirmations: the message a customer gets right after a ticket is created.

Receipts never wait for the LLM. The language is picked with a simple script check
(Myanmar and Thai have their own Unicode blocks), which is instant and cannot fail.
Edit the templates freely; {number} is replaced with the full ticket number.
"""
import re

from app.schemas import Language

_MYANMAR = re.compile(r"[\u1000-\u109F\uA9E0-\uA9FF\uAA60-\uAA7F]")
_THAI = re.compile(r"[\u0E00-\u0E7F]")

RECEIPT_TEMPLATES: dict[str, dict[str, str]] = {
    # Online channels: the customer is not in the shop.
    "default": {
        "my": "ဆက်သွယ်ပေးတဲ့အတွက် ကျေးဇူးတင်ပါတယ်။ သင့်လက်မှတ်နံပါတ်က {number} ဖြစ်ပါတယ်။ ဝန်ထမ်းက မကြာခင် ပြန်ဆက်သွယ်ပေးပါမယ်။",
        "th": "ขอบคุณที่ติดต่อเรา หมายเลขทิกเก็ตของคุณคือ {number} เจ้าหน้าที่จะติดต่อกลับโดยเร็ว",
        "en": "Thanks for contacting us. Your ticket number is {number}. Our staff will get back to you shortly.",
    },
    # Walk-in kiosk: the customer is standing at the counter.
    "walkin": {
        "my": "သင့်လက်မှတ်နံပါတ်က {number} ဖြစ်ပါတယ်။ ခဏထိုင်စောင့်ပေးပါ၊ နံပါတ်ခေါ်ပါမယ်။",
        "th": "หมายเลขทิกเก็ตของคุณคือ {number} กรุณานั่งรอสักครู่ เราจะเรียกหมายเลขของคุณ",
        "en": "Your ticket number is {number}. Please have a seat, and we'll call your number shortly.",
    },
}


def detect_language(text: str) -> Language:
    """Guess my / th / en from the characters used. Mixed text goes to the dominant script."""
    myanmar = len(_MYANMAR.findall(text))
    thai = len(_THAI.findall(text))
    if myanmar and myanmar >= thai:
        return "my"
    if thai:
        return "th"
    return "en"


def build_receipt(ticket_number: str, channel: str, language: Language) -> str:
    templates = RECEIPT_TEMPLATES.get(channel, RECEIPT_TEMPLATES["default"])
    return templates[language].format(number=ticket_number)


def short_number(ticket_number: str) -> str:
    """The daily sequence part staff call out loud: RBN-20260924-0007 -> 0007."""
    return ticket_number.rsplit("-", 1)[-1]
