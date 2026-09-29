"""Print every ticket with its status, triage result, route and messages.

Run from the project folder:  python show_tickets.py
"""
from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Ticket

init_db()
with SessionLocal() as s:
    tickets = s.scalars(select(Ticket).order_by(Ticket.id)).all()
    if not tickets:
        print("No tickets yet. Submit one at http://localhost:8000/kiosk")
    for t in tickets:
        print(f"\n{t.ticket_number}  {t.channel}  {t.customer.display_name or '-'}  [{t.status.value}]")
        if t.triage_results:
            r = t.triage_results[-1]
            print(f"   triage: {r.issue_type} | {r.urgency} | confidence {r.confidence:.2f} | route: {r.route}")
            print(f"   model:  {r.model_name}")
            print(f"   draft:  {r.draft_reply}")
        for m in t.messages:
            print(f"   {m.direction.value:<8} | {m.text}")
