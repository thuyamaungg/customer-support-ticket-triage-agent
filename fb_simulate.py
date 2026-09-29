"""Send a fake, correctly signed Facebook webhook to your local server.

Tests the whole Facebook path (signature check, parsing, ticket, triage) without Meta,
ngrok, or a Facebook page. Needs FB_APP_SECRET in .env (any value works for local tests).

Run from the project folder, with the server running:
    python fb_simulate.py "my wifi keeps disconnecting"
    python fb_simulate.py "ကွန်ပျူတာ ဖွင့်မရဘူး" --user PSID-2

The receipt send will fail (the user ID is fake) unless it's a real one. That's expected
and logged; the ticket is still created.
"""
import argparse
import hashlib
import hmac
import json
import time
import uuid

import httpx

from app.config import settings

parser = argparse.ArgumentParser()
parser.add_argument("text", help="the customer's message")
parser.add_argument("--user", default="PSID-TEST-1", help="fake sender ID (same ID = same customer)")
parser.add_argument("--url", default="http://localhost:8000/webhook/facebook")
args = parser.parse_args()

if not settings.fb_app_secret:
    raise SystemExit("Set FB_APP_SECRET in .env first (any value works for local testing), then restart the server.")

now = int(time.time() * 1000)
payload = {"object": "page", "entry": [{"id": "PAGE", "time": now, "messaging": [{
    "sender": {"id": args.user}, "recipient": {"id": "PAGE"}, "timestamp": now,
    "message": {"mid": f"mid.{uuid.uuid4().hex}", "text": args.text},
}]}]}
body = json.dumps(payload, ensure_ascii=False).encode()
signature = "sha256=" + hmac.new(settings.fb_app_secret.encode(), body, hashlib.sha256).hexdigest()

r = httpx.post(args.url, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature})
print(r.status_code, r.text)
print("Now run: python show_tickets.py")
