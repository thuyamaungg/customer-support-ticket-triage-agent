# Operations

Running, configuring, connecting and extending the ticket triage agent. For what the project is and why it's built this way, see the [README](../README.md).

---

## Running the server

```
.venv\Scripts\activate
uvicorn app.main:app --reload
```

On macOS or Linux: `source .venv/bin/activate`.

| What | Where |
|---|---|
| Walk-in kiosk | http://localhost:8000/kiosk |
| Admin API docs | http://localhost:8000/docs |

The server reads `.env` only at startup. **Restart after editing `.env`** — `--reload` watches Python files, not that one.

To reach the kiosk from a tablet on the same Wi-Fi:

```
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open `http://<your-PC's-IP>:8000/kiosk` on the tablet. Find the IP with `ipconfig` on Windows or `ip addr` on Linux, and allow Python through the firewall.

## Configuration

All settings come from `.env`. Start from `.env.example`.

| Setting | Default | Purpose |
|---|---|---|
| `TRIAGE_PROVIDER` | `gemini` | `gemini`, `openai` or `claude` |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | | Key for the chosen provider |
| `TRIAGE_MODEL` | provider default | Pin an exact model id |
| `TRIAGE_FALLBACK_MODELS` | provider defaults | Comma-separated backups; empty disables fallback |
| `TRIAGE_CONFIDENCE_THRESHOLD` | `0.7` | Below this, the ticket goes to a human |
| `REPLY_POLICY` | `hold_for_approval` | Or `auto_send_simple` |
| `ADMIN_API_KEY` | | Required for `/admin`; empty disables the admin API entirely |
| `STORE_NAME` | `Our Store` | Shown on the kiosk and used in the triage prompt |
| `TICKET_PREFIX` | `RBN` | First part of the ticket number |
| `TICKET_TIMEZONE` | `Asia/Yangon` | Decides when the daily counter resets |
| `FB_APP_SECRET` | | Verifies Facebook webhook signatures |
| `FB_VERIFY_TOKEN` | | Used once, when Meta verifies your callback URL |
| `FB_PAGE_ACCESS_TOKEN` | | Sends replies to Messenger |
| `KIOSK_RATE_LIMIT_PER_MINUTE` | `10` | Per IP — all walk-ins share one tablet, so this is a flood guard, not per-person |

### Reply policy

- `hold_for_approval` — every AI draft waits for a human to approve or edit it.
- `auto_send_simple` — straightforward, high-confidence cases are sent automatically. Anything routed to the owner, any complaint, custom quote or critical ticket, and anything low-confidence still waits.

Walk-in tickets are never auto-sent, whatever the policy: the customer is standing at the counter, not reading messages.

### Model ids drift

Providers retire models on their own schedule. If the triage log shows a `404` for a model id, replace it in `TRIAGE_MODEL` or `TRIAGE_FALLBACK_MODELS`.

<!-- CONFIRM: run `python try_triage.py` before publishing and update the defaults in app/config.py if any id 404s. -->

Quick check that your provider, key and model all work:

```
python try_triage.py
```

## Demo and inspection scripts

```
python seed_demo.py --offline    # Create sample tickets using canned triage results
python seed_demo.py              # Same, but run real triage (needs a key, costs tokens)
python show_tickets.py           # Print every ticket with its status and route
python try_triage.py             # Run the agent against sample messages and print the output
python fb_simulate.py "my wifi keeps disconnecting"   # Signed fake Facebook webhook
```

To start clean, stop the server, delete `triage.db`, restart, and re-seed. The database is recreated on startup.

## Facebook Messenger

### Test locally first

Put any value in `FB_APP_SECRET`, restart the server, then:

```
python fb_simulate.py "my wifi keeps disconnecting"
```

This signs a fake webhook with your secret and posts it to the local endpoint, so you can exercise the whole path — signature check, intake, receipt, triage, routing — without Meta involved.

### Connect a real page

Meta's dashboard is reorganised often, so menu names may differ.

1. Put a random string in `FB_VERIFY_TOKEN`.
2. Start the server: `uvicorn app.main:app --port 8000`
3. In a second terminal: `ngrok http 8000`, and copy the `https://...` address.
4. At developers.facebook.com, create an app and add the **Messenger** product.
5. **App settings → Basic**: copy the **App secret** into `FB_APP_SECRET`.
6. **Messenger API settings**: connect your page, generate a page token, put it in `FB_PAGE_ACCESS_TOKEN`.
7. Restart the server so the new values are read.
8. **Configure webhooks**: callback URL `https://<ngrok-address>/webhook/facebook`, verify token = your `FB_VERIFY_TOKEN`. Verify and save.
9. Subscribe the page to `messages` and `messaging_postbacks`.
10. Message the page. In development mode, only accounts with a role on the app can reach it.

The free ngrok address changes on every restart — update the callback URL in Meta's dashboard when it does.

### The 24-hour reply window

Messenger only accepts standard replies within 24 hours of the customer's last message. `GET /admin/tickets/{number}` reports `hours_left_to_reply`, and a send is refused once the window has closed rather than failing at the API.

## Admin API

Open http://localhost:8000/docs, click **Authorize**, and paste your `ADMIN_API_KEY`.

| Endpoint | Purpose |
|---|---|
| `GET /admin/tickets` | Open tickets, waiting-for-us and most urgent first. Filters: `status`, `route`, `include_closed` |
| `GET /admin/tickets/{number}` | Full conversation, triage labels, AI draft, reply window |
| `PUT /admin/tickets/{number}/draft` | Save an edited draft without sending it |
| `POST /admin/tickets/{number}/reply` | Send your text, or the stored draft if `text` is empty |
| `POST /admin/tickets/{number}/status` | Change status, e.g. to `closed` |
| `POST /admin/tickets/{number}/retriage` | Run triage again — useful after a `needs_manual_triage` |

If `ADMIN_API_KEY` is empty the admin routes are disabled rather than left open.

## Adding a channel

1. Fill in the stub in `app/channels/<platform>.py`, or copy an existing adapter. Implement:
   - `verify_request` — check the platform's signature
   - `parse_incoming` — return a list of `IncomingMessage`
   - `send_message` — raise `ChannelError` on failure
2. Make sure it's listed in `app/channels/__init__.py`.
3. Add a route in `app/webhooks.py` following `facebook_webhook`: read the body, verify, parse, call `handle_incoming(..., send_receipt=False)`, then queue the receipt and triage as background tasks.
4. If the platform restricts when you can reply, add it to `reply_window_hours` in `app/admin.py`.
5. Add tests modelled on `tests/test_facebook.py`.

Nothing else changes. Tickets, receipts, triage, routing and the admin API never learn which platform a message came from — that's the adapter's whole job.

## Connecting a specialist handler

`app/specialists.py` has one class per specialist domain. Replace the body of `suggest_reply` with a call to the backend service:

- Return a string to replace the triage draft with that reply.
- Return `None` to keep the triage draft.
- Raising is safe — the caller catches it, logs it, and keeps the draft.

The ticket records which specialist it was routed to either way, so routing behaviour can be checked before any backend exists.

## Testing

```
python -m pytest -q          # Quiet
python -m pytest -v          # Per-test names
python -m pytest tests/test_tickets.py -v      # One file
```

145 tests, roughly 11 seconds. The suite uses a fake LLM and mocked platform APIs — it never reaches the network and never spends money, even with live keys in `.env`.

| File | Covers |
|---|---|
| `test_tickets.py` | Intake, ticket numbering, concurrency (50 threads), duplicate handling |
| `test_triage.py` | Agent output, malformed-output retries, provider fallback, failure handling |
| `test_routing.py` | Routing rules and reply policy |
| `test_facebook.py` | Signature verification, parsing, duplicate deliveries |
| `test_walkin.py` | Kiosk intake and rate limiting |
| `test_receipts.py` | Receipt language selection and templates |
| `test_admin.py` | Admin auth, reply window, status changes |
| `test_config.py` | Settings loading and defaults |

## Troubleshooting

**`404` in the triage log.** The provider retired that model id. Replace it in `TRIAGE_MODEL` or `TRIAGE_FALLBACK_MODELS` and restart.

**Tickets stuck in `needs_manual_triage`.** The LLM call failed — usually a missing key, an exhausted quota, or every model in the fallback chain being unavailable. Fix the cause, then `POST /admin/tickets/{number}/retriage`.

**Changes to `.env` seem ignored.** The server reads it once at startup. Stop and restart; `--reload` doesn't watch it.

**`database is locked`.** Shouldn't happen — `db.py` sets `BEGIN IMMEDIATE` for this reason. If it does, check whether another process (a second server, an open DB browser) has `triage.db` open.

**Admin endpoints return 401.** The `ADMIN_API_KEY` header doesn't match. If the setting is empty in `.env`, the routes are disabled entirely.

**Facebook webhook verification fails.** The verify token in Meta's dashboard must match `FB_VERIFY_TOKEN` exactly, and the server must have been restarted since you set it. If signature checks fail afterwards, `FB_APP_SECRET` is wrong or stale.

**The kiosk rejects submissions.** `KIOSK_RATE_LIMIT_PER_MINUTE` is per IP, and every walk-in shares the tablet's IP. Raise it if a busy counter hits the limit.
