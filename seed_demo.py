"""Fill the database with sample tickets in Burmese, Thai and English for a demo.

Run from the project folder:
    python seed_demo.py            # real AI triage (uses your API key and quota)
    python seed_demo.py --offline  # no AI and no internet: ready-made triage results

No platform credentials are needed either way: receipts are recorded but not sent.
Afterwards: python show_tickets.py, or open http://localhost:8000/docs
"""
import argparse
import uuid

from app.channels.walkin import WalkInAdapter
from app.db import SessionLocal, init_db
from app.models import TriageResult
from app.routing import route_ticket
from app.schemas import IncomingMessage
from app.tickets import handle_incoming
from app.triage import run_triage

# (channel, name, message, offline triage: issue_type, urgency, confidence, language, draft reply)
SAMPLES = [
    ("walkin", "Ko Aung", "Laptop screen is cracked after it fell",
     ("hardware_repair", "medium", 0.94, "en", "Sorry about your screen! Please tell us the laptop brand and model so we can check it.")),
    ("facebook", None, "ကွန်ပျူတာ ဖွင့်လို့မရတော့ဘူး၊ မနက်ဖြန် စာမေးပွဲရှိတယ်",
     ("hardware_repair", "high", 0.93, "my", "အဆင်မပြေဖြစ်ရတဲ့အတွက် တောင်းပန်ပါတယ်။ ကွန်ပျူတာ အမှတ်တံဆိပ်နဲ့ မော်ဒယ်လ် ပြောပြပေးနိုင်မလား။")),
    ("facebook", None, "เน็ตหลุดทุกคืนเลยครับ",
     ("network_internet", "medium", 0.9, "th", "ขออภัยในความไม่สะดวกครับ รบกวนแจ้งว่าไฟที่เราเตอร์ขึ้นสีอะไรบ้างครับ")),
    ("walkin", "Ma Su", "wifi က ညနေတိုင်း အရမ်းနှေးတယ်",
     ("network_internet", "medium", 0.88, "my", "အင်တာနက် နှေးနေတဲ့အတွက် တောင်းပန်ပါတယ်။ ဘယ်အချိန်လောက်မှာ အဖြစ်များလဲ ပြောပြပေးနိုင်မလား။")),
    ("facebook", None, "RTX 4060 มีของไหมครับ ราคาเท่าไหร่",
     ("price_inquiry", "low", 0.96, "th", "ขอบคุณที่สนใจครับ เจ้าหน้าที่จะแจ้งราคาและสต็อกให้เร็วๆ นี้ครับ")),
    ("walkin", "U Tun", "You repaired my PC last week and it broke again. I want a refund.",
     ("complaint", "high", 0.95, "en", "We're very sorry it failed again. The shop owner will contact you personally.")),
    ("facebook", None, "Need 10 office PCs with MS Office, please send a quote",
     ("custom_quote", "low", 0.94, "en", "Happy to help! Do you also need monitors and a network setup for the office?")),
    ("facebook", None, "ဆိုင်မှာ အင်တာနက် မနက်ကတည်းက လုံးဝမရဘူး၊ ရောင်းလို့မရဘူး",
     ("network_internet", "critical", 0.97, "my", "တောင်းပန်ပါတယ်။ ဝန်ထမ်းက သင့်လိုင်းကို အမြန်ဆုံး စစ်ဆေးပေးပါမယ်။")),
    ("facebook", None, "hi",
     ("other", "low", 0.4, "en", "Hi! How can we help you today?")),
]

parser = argparse.ArgumentParser()
parser.add_argument("--offline", action="store_true", help="use ready-made triage results instead of the AI")
args = parser.parse_args()

init_db()
quiet = WalkInAdapter()  # records receipts without sending them anywhere
with SessionLocal() as session:
    for channel, name, text, (issue, urgency, confidence, lang, draft) in SAMPLES:
        user = f"demo-{uuid.uuid4().hex[:8]}"
        incoming = IncomingMessage(channel=channel, external_user_id=user, external_message_id=uuid.uuid4().hex,
                                   customer_name=name, text=text)
        ticket = handle_incoming(session, quiet, incoming).intake.ticket
        if args.offline:
            result = TriageResult(ticket=ticket, issue_type=issue, urgency=urgency, confidence=confidence,
                                  detected_language=lang, summary=text[:120], draft_reply=draft,
                                  reasoning="Demo data (offline).", model_name="offline-demo")
            session.add(result)
            session.commit()
            route_ticket(session, ticket, result)
        else:
            run_triage(session, ticket.id)
        latest = ticket.triage_results[-1] if ticket.triage_results else None
        outcome = f"{latest.issue_type} -> {latest.route}" if latest else "triage failed (try --offline)"
        print(f"{ticket.ticket_number}  {channel:<8}  {outcome}")

print("\nDone. Run: python show_tickets.py")
