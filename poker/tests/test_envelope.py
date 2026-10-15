from pathlib import Path

import pytest

from poker.parsing import EmailKind, MyReply, PrivateState, parse_email

FIX = Path(__file__).parent / "fixtures"


def _kind(path):
    return parse_email(path.read_bytes()).meta.kind


@pytest.mark.parametrize("path", sorted(FIX.glob("*Broadcast*.eml")), ids=lambda p: p.name[:12])
def test_broadcast_classification(path):
    assert _kind(path) is EmailKind.BROADCAST


def test_your_cards_classification():
    p = next(FIX.glob("Holdem game*.eml"))
    r = parse_email(p.read_bytes())
    assert r.meta.kind is EmailKind.YOUR_CARDS
    assert r.meta.from_addr == "dealer@wrgpt.org"
    assert r.meta.to_addrs == ("sefklon@gmail.com",)
    assert r.meta.subject == "Holdem game (ID=pikaro) [hand:2]: your cards"
    assert r.meta.date.utcoffset().total_seconds() == -7 * 3600
    assert r.meta.in_reply_to is None
    assert r.meta.message_id.startswith("<2026")


@pytest.mark.parametrize("path", sorted(FIX.glob("reminder*.eml")), ids=lambda p: p.name)
def test_my_reply_classification(path):
    r = parse_email(path.read_bytes())
    assert r.meta.kind is EmailKind.MY_REPLY
    assert r.meta.from_addr == "sefklon@gmail.com"
    assert r.meta.in_reply_to
    assert isinstance(r.body, MyReply)


def test_other_sender_is_unknown():
    raw = b"From: x@example.com\nTo: dealer@wrgpt.org\nSubject: hi\n\nhello\n"
    assert parse_email(raw).meta.kind is EmailKind.UNKNOWN


def test_dealer_reminder_classification():
    raw = b"From: dealer@wrgpt.org\nTo: sefklon@gmail.com\nSubject: reminder (ID=abc)[hand:3]\n\n! x\n"
    r = parse_email(raw)
    assert r.meta.kind is EmailKind.REMINDER
    assert r.body is None and r.errors  # malformed body is recorded, not raised


@pytest.mark.parametrize("raw", [b"", b"\xff\xfe\x00garbage", b"From: \nSubject\n\n\x00\x01", b"Date: nonsense\n\n"])
def test_garbage_never_raises(raw):
    r = parse_email(raw)
    assert r.meta.kind is EmailKind.UNKNOWN or r.errors


def test_broadcast_dispatch():
    pytest.importorskip("poker.parsing.broadcast")
    p = next(FIX.glob("*10014*.eml"))
    r = parse_email(p.read_bytes())
    assert r.errors == () and r.body is not None
