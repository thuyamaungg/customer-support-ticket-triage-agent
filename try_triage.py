"""Try the AI triage on sample messages, using your real API key from .env.

Run from the project folder:   python try_triage.py
Or classify your own message:  python try_triage.py "my printer is jammed"

Nothing is saved to the database. The provider comes from TRIAGE_PROVIDER in .env.
"""
import sys

from app.config import settings
from app.triage import describe_error, get_agent

SAMPLES = [
    "My laptop won't turn on since this morning, I have an exam tomorrow",
    "wifi က ညနေတိုင်း အရမ်းနှေးတယ်",
    "RTX 4060 มีของไหมครับ ราคาเท่าไหร่",
    "I paid for a repair 2 weeks ago and still nothing. Very disappointed.",
    "hello",
]

messages = sys.argv[1:] or SAMPLES
print("Models, in the order they are tried:", ", ".join(settings.triage_model_chain))
try:
    agent = get_agent()
except Exception as exc:
    sys.exit(f"Could not set up any model: {exc}")
for text in messages:
    prompt = f"Ticket TEST, received via facebook. Customer name: Test.\n<customer_messages>\nMessage 1: {text}\n</customer_messages>"
    try:
        run = agent.run_sync(prompt)
        out = run.output
    except Exception as exc:
        print(f"\n{text}\n  FAILED:")
        for reason in describe_error(exc).split(" | "):
            print(f"    - {reason}")
        continue
    print(f"\n{text}   [answered by {run.response.model_name}]")
    print(f"  {out.issue_type} | {out.urgency} | confidence {out.confidence:.2f} | {out.detected_language}")
    print(f"  summary: {out.summary}")
    print(f"  reply:   {out.draft_reply}")
