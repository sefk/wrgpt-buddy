from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from poker import state
from poker.models import MyHand, RawEmail
from poker.parsing import AdvanceAck, DealerError, EmailKind, EventKind, parse_email
from poker.tests.test_state import bcast, ev, mkraw, parsed, run

FIX = Path(__file__).parent / "fixtures" / "dealer"
T0 = datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)


def load(name):
    return parse_email((FIX / name).read_bytes())


def test_ack_sticky():
    r = load("ack_sticky_b17_h3.eml")
    assert r.errors == () and r.meta.kind is EmailKind.ADVANCE_ACK
    assert r.body == AdvanceAck("b17", 3, 4, "vimuho", True, "Check if $0 to you, fold otherwise", None)


@pytest.mark.parametrize("name,table,hand,day", [("ack_sticky_a38_h4.eml", "a38", 4, 4), ("ack_sticky_b17_h3.eml", "b17", 3, 4)])
def test_ack_sticky_variants(name, table, hand, day):
    b = load(name).body
    assert (b.table, b.hand_number, b.day, b.sticky) == (table, hand, day, True)


def test_ack_nonsticky():
    b = load("ack_nonsticky_a31_h27.eml").body
    assert not b.sticky and b.game_id == "pikaro"
    assert b.description == "Adding $1,200 to your previous action, get a choice if insufficient"
    assert b.table_talk is None


def test_error():
    r = load("error_a31_h36.eml")
    assert r.errors == () and r.meta.kind is EmailKind.DEALER_ERROR
    assert r.body == DealerError("a31", 36, 24, "pikaro", "missing bet(perhaps forgot $-sign)")


def test_other_dealer_mail_stays_unknown():
    for n in ("start_of_game.eml", "echo_missing_money.eml"):
        r = load(n)
        assert r.meta.kind is EmailKind.UNKNOWN and r.body is None


def test_malformed_ack_reports_error():
    from poker.parsing.dealer import parse_advance_ack

    with pytest.raises(ValueError):
        parse_advance_ack("Holdem game (ID=x) [hand:1] Advance action acknowledgement", "! Table a1, Hand 1, Day 1\n")


# --- state ---

pytestmark = pytest.mark.django_db

def apply_fix(name, mid, minutes):
    p = load(name)
    raw = mkraw(mid, minutes)
    state.apply(raw, p)
    return raw


def test_state_ack_and_error():
    raw = apply_fix("ack_nonsticky_a31_h27.eml", "a", 0)
    m = MyHand.objects.get()
    assert (m.advance_sticky, m.game_id, m.hand.number) == (False, "pikaro", 27)
    assert m.advance_action.startswith("Adding $1,200") and m.advance_action_at == raw.sent_at
    assert not m.has_unresolved_error


def test_error_unresolved_until_command():
    p = load("error_a31_h36.eml")
    state.apply(mkraw("e", 5), p)
    m = MyHand.objects.get()
    assert m.last_error == "missing bet(perhaps forgot $-sign)" and m.has_unresolved_error
    m.last_command_at = T0 + timedelta(minutes=9)
    assert not m.has_unresolved_error


def test_idempotent_and_out_of_order():
    a_new = AdvanceAck("b17", 3, 4, "g", True, "NEW", None)
    a_old = AdvanceAck("b17", 3, 4, "g", False, "OLD", None)
    for _ in range(2):
        state.apply(mkraw("n", 10) if _ == 0 else RawEmail.objects.get(message_id="n"), parsed("n", a_new, EmailKind.ADVANCE_ACK, 10))
    state.apply(mkraw("o", 5), parsed("o", a_old, EmailKind.ADVANCE_ACK, 5))
    m = MyHand.objects.get()
    assert (m.advance_action, m.advance_sticky) == ("NEW", True)
    e_new = DealerError("b17", 3, 4, "g", "NEW")
    e_old = DealerError("b17", 3, 4, "g", "OLD")
    state.apply(mkraw("en", 10), parsed("en", e_new, EmailKind.DEALER_ERROR, 10))
    state.apply(mkraw("eo", 5), parsed("eo", e_old, EmailKind.DEALER_ERROR, 5))
    assert MyHand.objects.get().last_error == "NEW"
    assert MyHand.objects.count() == 1


def _ack(sticky, minutes=0, text="Check if $0 to you, fold otherwise"):
    state.apply(mkraw(f"ack{minutes}", minutes), parsed(f"ack{minutes}", AdvanceAck("b17", 2, 3, "g", sticky, text, None), EmailKind.ADVANCE_ACK, minutes))


def _mine(seq, minutes, player="sefk", hand_over=False):
    events = [ev(EventKind.FOLD, player)]
    run(f"b{seq}", bcast(seq, events, hand_over=hand_over), minutes)


def test_nonsticky_cleared_after_my_action():
    _ack(False, 0)
    run("bx", bcast(1, [ev(EventKind.CALL, "Ann")]), 5)  # someone else acts: keep
    assert MyHand.objects.get().advance_action
    run("bold", bcast(2, [ev(EventKind.FOLD, "sefk")]), -5)  # before the ack: keep
    assert MyHand.objects.get().advance_action
    _mine(3, 10)
    m = MyHand.objects.get()
    assert m.advance_action == "" and not m.advance_sticky


def test_sticky_kept_until_hand_over():
    _ack(True, 0)
    _mine(1, 10)
    assert MyHand.objects.get().advance_action
    run("end", bcast(2, [], hand_over=True), 20)
    assert MyHand.objects.get().advance_action == ""
