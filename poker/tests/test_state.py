from datetime import datetime, timedelta, timezone

import pytest

from poker import state
from poker.models import Action, Hand, MyHand, RawEmail
from poker.parsing.types import (
    Broadcast,
    EmailKind,
    EmailMeta,
    EventKind,
    HandEvent,
    MyReply,
    ParsedEmail,
    PrivateState,
    SeatRow,
)

T0 = datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.django_db


def seat(n, name, bank, **kw):
    d = dict(seat=n, name=name, bankroll=bank, action=0, status="", is_dealer=False,
             is_next=False, is_vacation=False)
    d.update(kw)
    return SeatRow(**d)


def meta(mid, kind=EmailKind.BROADCAST, minutes=0):
    return EmailMeta(mid, "s", "d@x", ("me@x",), T0 + timedelta(minutes=minutes), None, kind)


def mkraw(mid, minutes=0):
    return RawEmail.objects.create(message_id=mid, raw=b"x", sent_at=T0 + timedelta(minutes=minutes))


def bcast(seq, events=(), **kw):
    d = dict(table="b17", hand_number=2, day=3, sequence=seq, addressed_to="a", events=tuple(events),
             history=(), next_to_act="Ann", to_call=100, pot=300, next_timeout=None,
             betting_round=1, board=(None,) * 5, seats=(seat(1, "Ann", 900), seat(2, "Bob", 1100)),
             final_seats=(), hand_over=False)
    d.update(kw)
    return Broadcast(**d)


def ev(kind, player=None, text="", **kw):
    return HandEvent(kind=kind, text=text or f"{player} {kind}", player=player, **kw)


def parsed(mid, body, kind=EmailKind.BROADCAST, minutes=0):
    return ParsedEmail(meta=meta(mid, kind, minutes), body=body)


def run(mid, body, minutes=0, kind=EmailKind.BROADCAST):
    raw = mkraw(mid, minutes)
    state.apply(raw, parsed(mid, body, kind, minutes))
    return raw


def snapshot():
    h = Hand.objects.get()
    return (
        h.last_sequence, h.pot, h.board, h.is_over, h.next_to_act and h.next_to_act.name, h.betting_round,
        list(Action.objects.values_list("kind", "player__name", "order", "betting_round")),
        list(h.seats.values_list("player__name", "bankroll_start", "bankroll_end")),
    )


def test_basic_broadcast():
    run("m1", bcast(10, [ev(EventKind.CALL, "Bob")]))
    h = Hand.objects.get()
    assert (h.table.name, h.number, h.pot, h.to_call, h.next_to_act.name) == ("b17", 2, 300, 100, "Ann")
    assert h.actions.count() == 1 and h.seats.count() == 2


def test_idempotent():
    body = bcast(10, [ev(EventKind.CALL, "Bob"), ev(EventKind.FOLD, "Ann")])
    raw = run("m1", body)
    before = snapshot()
    state.apply(raw, parsed("m1", body))
    assert snapshot() == before


def test_idempotent_history_and_private():
    hist = (ev(EventKind.NEW_HAND, text="Dealing", timestamp=T0), ev(EventKind.CALL, "Bob", timestamp=T0))
    raw = run("m1", bcast(20, history=hist, hand_over=True))
    before = snapshot()
    state.apply(raw, parsed("m1", bcast(20, history=hist, hand_over=True)))
    assert snapshot() == before
    p = PrivateState(EmailKind.YOUR_CARDS, "g1", "b17", 2, 3, ("9d", "9s"))
    r = run("p1", p, kind=EmailKind.YOUR_CARDS)
    state.apply(r, parsed("p1", p, EmailKind.YOUR_CARDS))
    assert MyHand.objects.count() == 1


def test_out_of_order_keeps_latest_state():
    final = bcast(30, [ev(EventKind.CALL, "Bob")], pot=900, to_call=None, next_to_act=None,
                  betting_round=3, board=("As", "6c", "Th", None, None), hand_over=True,
                  final_seats=(seat(1, "Ann", 800), seat(2, "Bob", 1200)))
    run("m3", final, 3)
    run("m1", bcast(10, [ev(EventKind.FOLD, "Ann")]), 1)
    run("m2", bcast(20, [ev(EventKind.BET, "Bob", amount=50)], pot=500), 2)
    h = Hand.objects.get()
    assert (h.last_sequence, h.pot, h.is_over, h.next_to_act, h.betting_round) == (30, 900, True, None, 3)
    assert h.board == "As 6c Th"
    assert h.actions.count() == 3
    assert [a.kind for a in h.actions.order_by("order")] == ["fold", "bet", "call"]
    assert {s.player.name: s.bankroll_end for s in h.seats.all()} == {"Ann": 800, "Bob": 1200}


def test_history_replaces_incremental_and_tracks_rounds():
    run("m1", bcast(10, [ev(EventKind.CALL, "Bob")]), 1)
    hist = (
        ev(EventKind.NEW_HAND, timestamp=T0),
        ev(EventKind.CALL, "Bob", timestamp=T0 + timedelta(hours=1)),
        ev(EventKind.POT_RIGHT, amount=300, timestamp=T0 + timedelta(hours=2)),
        ev(EventKind.BET, "Ann", amount=50, timestamp=T0 + timedelta(hours=3)),
        ev(EventKind.WINS, "Ann", text="Ann wins $400", amount=400),
    )
    run("m9", bcast(40, history=hist, hand_over=True), 9)
    h = Hand.objects.get()
    acts = list(h.actions.order_by("order"))
    assert [a.kind for a in acts] == ["new_hand", "call", "pot_right", "bet", "wins"]
    assert [a.betting_round for a in acts] == [1, 1, 2, 2, 2]
    assert acts[1].timestamp is not None
    assert h.started_at == T0 and h.ended_at == T0 + timedelta(hours=3)
    assert "Ann wins" in h.winner
    # a late incremental must not pollute the authoritative log
    run("m5", bcast(15, [ev(EventKind.FOLD, "Ann")]), 5)
    assert h.actions.count() == 5


def test_incremental_betting_round_bump():
    evs = [ev(EventKind.CALL, "Bob"), ev(EventKind.POT_RIGHT, amount=300), ev(EventKind.BET, "Ann", amount=5)]
    run("m1", bcast(10, evs, betting_round=2))
    assert [a.betting_round for a in Hand.objects.get().actions.order_by("order")] == [1, 2, 2]


def test_private_and_reply():
    p = PrivateState(EmailKind.YOUR_CARDS, "g1", "a38", 7, 2, ("Ah", "Kh"), to_call=50, pot=200)
    run("p1", p, 0, EmailKind.YOUR_CARDS)
    mh = MyHand.objects.get()
    assert (mh.hand.table.name, mh.hand.number, mh.hole_cards, mh.to_call) == ("a38", 7, "Ah Kh", 50)

    q = PrivateState(EmailKind.REMINDER, "g2", "b17", 2, 3, ("9d", "9s"), to_call=100)
    run("r1", MyReply("call", 2, "g2", q), 10, EmailKind.MY_REPLY)
    run("r0", MyReply("fold", 2, "g2", q), 5, EmailKind.MY_REPLY)  # older, arrives later
    m = MyHand.objects.get(hand__table__name="b17")
    assert (m.hole_cards, m.last_command, m.game_id) == ("9d 9s", "call", "g2")


def test_reply_without_quote_uses_game_id():
    q = PrivateState(EmailKind.REMINDER, "g2", "b17", 2, 3, ("9d", "9s"))
    run("p", q, 0, EmailKind.REMINDER)
    run("r", MyReply("check", None, "g2", None), 1, EmailKind.MY_REPLY)
    assert MyHand.objects.get().last_command == "check"


def test_rebuild_all():
    for i, (mid, body) in enumerate([("m1", bcast(10, [ev(EventKind.CALL, "Bob")])),
                                     ("m2", bcast(20, [ev(EventKind.FOLD, "Ann")], pot=999))]):
        mkraw(mid, i)
    bodies = {"m1": bcast(10, [ev(EventKind.CALL, "Bob")]), "m2": bcast(20, [ev(EventKind.FOLD, "Ann")], pot=999)}
    fake = lambda raw: parsed(raw.message_id, bodies[raw.message_id])
    assert state.rebuild_all(parse=fake) == 2
    snap = snapshot()
    state.rebuild_all(parse=fake)
    assert snapshot() == snap and Hand.objects.get().pot == 999


def test_finished_hand_stays_finished_after_stray_broadcast():
    from poker.parsing.types import Broadcast, EventKind, HandEvent

    def bc(seq, over, events=()):
        return Broadcast(
            table="a31", hand_number=36, day=24, sequence=seq, addressed_to=None,
            events=tuple(events), history=(), next_to_act=None, to_call=None, pot=None,
            next_timeout=None, betting_round=None, board=None, seats=(), final_seats=(),
            hand_over=over,
        )

    from poker.models import Hand, RawEmail
    from poker import state
    from poker.parsing.types import EmailKind, EmailMeta, ParsedEmail

    def apply(seq, over, events=()):
        raw = RawEmail.objects.create(message_id=f"<{seq}>", raw=b"")
        meta = EmailMeta(f"<{seq}>", "", "dealer@wrgpt.org", (), None, None, EmailKind.BROADCAST)
        state.apply(raw, ParsedEmail(meta, bc(seq, over, events)))

    win = HandEvent(EventKind.WINS, "sefk wins $89,700 (net $16,000)", player="sefk", amount=89700)
    apply(10393, True, [win])
    apply(10394, False, [HandEvent(EventKind.OTHER, "Player leaving table: Jim Anderson")])
    h = Hand.objects.get(table__name="a31", number=36)
    assert h.is_over and h.last_sequence == 10394


def test_start_bankrolls_come_from_the_earliest_broadcast_whatever_the_order():
    run("late", bcast(20, seats=(seat(1, "Ann", 700), seat(2, "Bob", 1100))), 10)
    run("early", bcast(10, seats=(seat(1, "Ann", 900), seat(2, "Bob", 1100))), 0)
    h = Hand.objects.get()
    assert dict(h.seats.values_list("player__name", "bankroll_start")) == {"Ann": 900, "Bob": 1100}
    assert h.pot == 300 and h.last_sequence == 20


def test_final_broadcast_keeps_the_hands_button_and_records_end_bankrolls():
    run("m1", bcast(10, seats=(seat(1, "Ann", 900, is_dealer=True), seat(2, "Bob", 1100))), 0)
    final = bcast(11, hand_over=True,
                  seats=(seat(1, "Ann", 900, is_dealer=True), seat(2, "Bob", 1100)),
                  final_seats=(seat(1, "Ann", 1200), seat(2, "Bob", 800, is_dealer=True)))
    run("m2", final, 1)
    seats = {s.player.name: s for s in Hand.objects.get().seats.all()}
    assert seats["Ann"].is_dealer and not seats["Bob"].is_dealer
    assert (seats["Ann"].bankroll_end, seats["Bob"].bankroll_end) == (1200, 800)


def test_unquoted_reply_matches_its_hand_number():
    for n, mid in ((1, "p1"), (2, "p2")):
        run(mid, PrivateState(EmailKind.REMINDER, "g2", "b17", n, 3, ("9d", "9s")), n, EmailKind.REMINDER)
    run("r", MyReply("check", 1, "g2", None), 5, EmailKind.MY_REPLY)
    cmds = dict(MyHand.objects.values_list("hand__number", "last_command"))
    assert cmds == {1: "check", 2: ""}
