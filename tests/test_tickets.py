from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models import Message, Ticket, TicketStatus
from app.schemas import IncomingMessage
from app.tickets import intake_message, next_ticket_number

DAY = datetime(2026, 9, 24, 3, 0, tzinfo=timezone.utc)  # 09:30 in Yangon


def msg(user="u1", text="My laptop won't turn on", channel="facebook", mid=None, name=None):
    return IncomingMessage(channel=channel, external_user_id=user, text=text,
                           external_message_id=mid, customer_name=name)


# ---- ticket numbers -------------------------------------------------------

def test_ticket_number_format_and_sequence(session):
    assert next_ticket_number(session, now=DAY) == "RBN-20260924-0001"
    assert next_ticket_number(session, now=DAY) == "RBN-20260924-0002"


def test_counter_resets_each_day(session):
    next_ticket_number(session, now=DAY)
    next_day = datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc)
    assert next_ticket_number(session, now=next_day) == "RBN-20260925-0001"


def test_day_follows_store_timezone_not_utc(session):
    # 18:00 UTC on the 24th is already 00:30 on the 25th in Yangon (UTC+6:30).
    late_utc = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)
    assert next_ticket_number(session, now=late_utc) == "RBN-20260925-0001"


def test_concurrent_ticket_numbers_are_unique(session_factory):
    def grab(_):
        with session_factory() as s:
            number = next_ticket_number(s, now=DAY)
            s.commit()
            return number

    with ThreadPoolExecutor(max_workers=10) as pool:
        numbers = list(pool.map(grab, range(50)))

    assert len(set(numbers)) == 50
    assert sorted(int(n.rsplit("-", 1)[1]) for n in numbers) == list(range(1, 51))


# ---- intake ---------------------------------------------------------------

def test_first_message_creates_ticket(session):
    result = intake_message(session, msg(mid="m1", name="Aung"), now=DAY)
    session.commit()
    assert result.is_new_ticket and not result.is_duplicate
    assert result.ticket.ticket_number == "RBN-20260924-0001"
    assert result.ticket.status is TicketStatus.NEW
    assert result.ticket.customer.display_name == "Aung"


def test_follow_up_appends_to_open_ticket(session):
    first = intake_message(session, msg(mid="m1"), now=DAY)
    second = intake_message(session, msg(mid="m2", text="Also the screen flickers"), now=DAY)
    session.commit()
    assert not second.is_new_ticket
    assert second.ticket.id == first.ticket.id
    assert len(first.ticket.messages) == 2


def test_closed_ticket_means_new_ticket(session):
    first = intake_message(session, msg(mid="m1"), now=DAY)
    first.ticket.status = TicketStatus.CLOSED
    session.flush()
    second = intake_message(session, msg(mid="m2"), now=DAY)
    assert second.is_new_ticket
    assert second.ticket.ticket_number == "RBN-20260924-0002"


def test_duplicate_delivery_is_ignored(session):
    first = intake_message(session, msg(mid="m1"), now=DAY)
    again = intake_message(session, msg(mid="m1"), now=DAY)
    session.commit()
    assert again.is_duplicate
    assert again.message.id == first.message.id
    assert session.scalar(select(func.count(Message.id))) == 1
    assert session.scalar(select(func.count(Ticket.id))) == 1


def test_same_user_id_on_other_channel_is_separate(session):
    a = intake_message(session, msg(channel="facebook", mid="m1"), now=DAY)
    b = intake_message(session, msg(channel="telegram", mid="m1"), now=DAY)
    assert a.ticket.id != b.ticket.id


def test_walkin_without_message_id_still_works(session):
    result = intake_message(session, msg(channel="walkin", user="kiosk-abc", mid=None, name="Ma Hla"), now=DAY)
    assert result.is_new_ticket


def test_concurrent_intake_from_many_customers(session_factory):
    def send(i):
        with session_factory() as s:
            r = intake_message(s, msg(user=f"user{i}", mid=f"m{i}"), now=DAY)
            s.commit()
            return r.ticket.ticket_number

    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = list(pool.map(send, range(20)))
    assert len(set(numbers)) == 20


def test_concurrent_duplicate_deliveries_store_one_message(session_factory):
    def send(_):
        with session_factory() as s:
            r = intake_message(s, msg(mid="same-id"), now=DAY)
            s.commit()
            return r.ticket.ticket_number

    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = set(pool.map(send, range(10)))
    assert numbers == {"RBN-20260924-0001"}
    with session_factory() as s:
        assert s.scalar(select(func.count(Message.id))) == 1


# ---- input validation -----------------------------------------------------

def test_text_is_stripped_and_blank_rejected():
    assert msg(text="  hello  ").text == "hello"
    with pytest.raises(ValidationError):
        msg(text="   ")


def test_overlong_text_rejected():
    with pytest.raises(ValidationError):
        msg(text="x" * 5000)


def test_burmese_and_thai_text_accepted():
    assert msg(text="ကွန်ပျူတာ ဖွင့်မရပါ").text
    assert msg(text="คอมเปิดไม่ติดครับ").text
