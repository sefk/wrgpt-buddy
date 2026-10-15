from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from poker.parsing import EmailKind, parse_email, parse_my_reply, parse_private

FIX = Path(__file__).parent / "fixtures"
LA = ZoneInfo("America/Los_Angeles")


def test_your_cards():
    r = parse_email(next(FIX.glob("Holdem game*.eml")).read_bytes())
    s = r.body
    assert r.errors == ()
    assert s.kind is EmailKind.YOUR_CARDS
    assert (s.game_id, s.table, s.hand_number, s.day) == ("pikaro", "a38", 2, 2)
    assert s.hole_cards == ("4h", "2h")
    assert s.to_call is None and s.board is None
    assert s.status_url == "http://hands.wrgpt.org/a/a38.html"


def test_reply_fold():
    r = parse_email((FIX / "reminder (ID=vimuho)[hand_2].eml").read_bytes())
    b = r.body
    assert b.command == "Fold"
    assert (b.hand_number, b.game_id) == (2, "vimuho")
    q = b.quoted
    assert q.kind is EmailKind.REMINDER
    assert (q.table, q.day, q.hand_number) == ("b17", 3, 2)
    assert q.hole_cards == ("9d", "9s")
    assert q.to_call == 600 and q.pot == 4650
    assert q.board == ("As", "6c", "Th", None, None)
    assert q.next_timeout == datetime(2026, 10, 15, 8, 3, tzinfo=LA)
    assert q.start_time == 7
    assert q.status_url == "https://hands.wrgpt.org/b/b17.html"


def test_reply_call():
    r = parse_email((FIX / "reminder (ID=vimuho)[hand_2]_1.eml").read_bytes())
    b = r.body
    assert b.command == "call *"
    q = b.quoted
    assert q.day == 2 and q.to_call == 650 and q.pot == 1600
    assert q.board == (None,) * 5
    assert q.next_timeout == datetime(2026, 10, 14, 11, 1, tzinfo=LA)


def test_synthetic_dealer_reminder():
    r = parse_email((FIX / "reminder (ID=vimuho)[hand_2].eml").read_bytes())
    # rebuild the quoted text without "> " prefixes
    text = (FIX / "reminder (ID=vimuho)[hand_2].eml").read_bytes()
    import email, email.policy
    body = email.message_from_bytes(text, policy=email.policy.default).get_body(("plain",)).get_content()
    dequoted = "\n".join(l[2:] if l.startswith("> ") else l.lstrip(">") for l in body.splitlines() if l.startswith(">"))
    s = parse_private("reminder (ID=vimuho)[hand:2]", dequoted)
    assert s == r.body.quoted


def test_reply_without_quote():
    m = parse_my_reply("Re: reminder (ID=zzz)[hand:5]", "\n\nraise 200\n")
    assert m.command == "raise 200" and m.quoted is None and m.hand_number == 5
