import email
import email.policy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from poker.parsing.broadcast import parse_broadcast
from poker.parsing.types import EventKind

FIXTURES = Path(__file__).parent / "fixtures" / "hand_2"
TZ = ZoneInfo("America/Los_Angeles")
FILES = sorted(FIXTURES.glob("[[]b17.100*hand_2] Holdem Game Broadcast*.eml"))


def load(path):
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)
    body = msg.get_body(preferencelist=("plain",)).get_content()
    return parse_broadcast(msg["Subject"], body)


def by_seq(n):
    (path,) = [p for p in FILES if f"b17.{n}]" in p.name]
    return load(path)


def test_fixture_count():
    assert len(FILES) == 19


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name[:11])
def test_all_parse(path):
    b = load(path)
    assert b.table == "b17"
    assert b.hand_number == 2
    assert len(b.seats) == 9
    assert b.status_url == "https://hands.wrgpt.org/b/b17.html"
    assert str(b.sequence) in path.name


def test_10014_start_of_hand():
    b = by_seq(10014)
    assert b.sequence == 10014
    assert b.day == 2
    assert b.addressed_to == "Mark Ballinger"
    assert [e.kind for e in b.events] == [
        EventKind.NEW_HAND,
        EventKind.ANTE,
        EventKind.BLIND,
        EventKind.BLIND,
        EventKind.VACATION_FOLD,
    ]
    assert (b.events[2].player, b.events[2].amount) == ("Two_Jacks", 100)
    assert (b.events[3].player, b.events[3].amount) == ("Michael Rankin", 200)
    assert b.events[4].player == "Options Dee"
    assert b.next_to_act == "Mark Ballinger"
    assert b.to_call == 200
    assert b.pot == 300
    assert b.betting_round == 1
    assert b.board == (None,) * 5
    assert b.next_timeout == datetime(2026, 10, 14, 8, 34, tzinfo=TZ)
    assert len(b.seats) == 9
    dealer = [s for s in b.seats if s.is_dealer]
    assert [(s.seat, s.name) for s in dealer] == [(2, "Bryan Currie")]
    assert [s.seat for s in b.seats if s.is_vacation] == [5]
    assert [s.name for s in b.seats if s.is_next] == ["Mark Ballinger"]
    s1 = b.seats[0]
    assert (s1.name, s1.bankroll, s1.action, s1.status) == ("sefk", 60300, 0, "")
    assert b.seats[2].action == 100
    assert b.seats[4].status == "NoSho"
    assert b.history == () and b.final_seats == () and not b.hand_over


def test_10024_mid_hand():
    b = by_seq(10024)
    assert b.day == 3
    assert [(e.kind, e.player) for e in b.events] == [(EventKind.FOLD, "RiverRat")]
    assert b.board == ("As", "6c", "Th", None, None)
    assert b.betting_round == 2
    assert b.next_to_act == "sefk"
    assert b.to_call == 600
    assert b.pot == 4650
    assert b.next_timeout == datetime(2026, 10, 15, 8, 3, tzinfo=TZ)
    assert b.seats[2].action == 1250
    assert b.seats[3].status == "folded"
    assert b.seats[0].is_next and not b.seats[0].is_dealer


def test_first_to_act_and_checked_to():
    b = by_seq(10021)
    assert b.next_to_act == "Two_Jacks"
    assert b.to_call is None
    kinds = [e.kind for e in b.events]
    assert kinds == [
        EventKind.FOLD,
        EventKind.POT_RIGHT,
        EventKind.PLAYERS_LEFT,
        EventKind.BOARD_CARDS,
    ]
    assert b.events[1].amount == 3450
    assert b.events[2].amount == 5
    assert b.events[3].cards == ("As", "6c", "Th")
    b = by_seq(10030)
    assert [e.kind for e in b.events] == [EventKind.CHECK]
    # "Checked to X" is the only sign of whose turn it is after a check.
    assert b.next_to_act == "Capparella" and b.to_call is None
    assert by_seq(10031).next_to_act == "Bryan Currie"


def test_10034_final():
    b = by_seq(10034)
    assert b.addressed_to is None
    assert b.day == 4
    assert b.hand_over
    assert [(e.kind, e.player) for e in b.events] == [(EventKind.CALL, "Capparella")]
    assert any(s.is_dealer and s.name == "Bryan Currie" and s.is_next for s in b.seats)
    assert b.seats[1].action == 5550

    h = b.history
    assert len(h) == 39
    assert h[0].kind is EventKind.NEW_HAND
    assert h[0].timestamp == datetime(2026, 10, 13, 14, 33, 24, tzinfo=TZ)
    assert h[0].text == "Dealing a new hand"
    raise_ev = [e for e in h if e.kind is EventKind.RAISE]
    assert len(raise_ev) == 1
    r = raise_ev[0]
    assert (r.player, r.amount, r.total) == ("Capparella", 450, 650)
    assert r.timestamp == datetime(2026, 10, 13, 16, 5, 11, tzinfo=TZ)
    assert [e.amount for e in h if e.kind is EventKind.BET] == [600, 800, 3500]
    assert [e.cards for e in h if e.kind is EventKind.BOARD_CARDS] == [
        ("As", "6c", "Th"),
        ("4c",),
        ("2h",),
    ]
    assert [e.amount for e in h if e.kind is EventKind.POT_RIGHT] == [3450, 5250, 7650]
    assert [e.amount for e in h if e.kind is EventKind.PLAYERS_LEFT] == [5, 3, 3]
    sb = [e for e in h if e.kind is EventKind.SHOWDOWN_BOARD][0]
    assert sb.cards == ("As", "6c", "Th", "4c", "2h")
    assert sb.timestamp is None
    shows = {e.player: e.cards for e in h if e.kind is EventKind.SHOWS}
    assert shows == {"Bryan Currie": ("Kh", "Jh"), "Capparella": ("Ah", "8h")}
    (w,) = [e for e in h if e.kind is EventKind.WINS]
    assert (w.player, w.amount, w.net) == ("Capparella", 14650, 9100)
    assert "One Pair (A A T 8 6)" in w.text
    assert h[-1] is w
    assert not any(e.kind is EventKind.OTHER for e in h)

    assert len(b.final_seats) == 9
    cap = [s for s in b.final_seats if s.name == "Capparella"][0]
    assert cap.bankroll == 69100 and cap.action is None
    assert [s.name for s in b.final_seats if s.is_dealer] == ["Two_Jacks"]
    assert [s.name for s in b.final_seats if s.is_next] == ["Bryan Currie"]
    assert b.notes == (
        "Aardvark timed out during this hand",
        "456 players left in the tournament, 0 tables down to six players",
        "A new hand will be dealt shortly",
    )
    assert b.status_url == "https://hands.wrgpt.org/b/b17.html"


def test_bad_subject():
    with pytest.raises(ValueError):
        parse_broadcast("hello", "! Table b17, Hand 2, Day 3\n")


def test_unknown_line_is_other():
    body = "! Table b17, Hand 2, Day 3\n\n! Something weird happened\n! Mark folds\n"
    b = parse_broadcast("[b17.1][hand:2] Holdem Game Broadcast Message(*)", body)
    assert [e.kind for e in b.events] == [EventKind.OTHER, EventKind.FOLD]
    assert b.events[0].text == "Something weird happened"


def test_incremental_events_match_history():
    """Concatenated incremental events == history events (modulo gaps)."""
    seq = sorted((load(p) for p in FILES), key=lambda b: b.sequence)
    inc = [(e.kind, e.player) for b in seq for e in b.events]
    hist = [(e.kind, e.player) for e in seq[-1].history if e.kind is not EventKind.SHOWDOWN_BOARD]
    hist = [
        x
        for x in hist
        if x[0] not in (EventKind.SHOWS, EventKind.WINS, EventKind.SHOWDOWN_BOARD)
    ]
    # Every incremental event appears in order in the history.
    it = iter(hist)
    assert all(any(x == h for h in it) for x in inc)
    # The only history events never seen incrementally are the ones in the
    # missing broadcasts (sequence gaps 10016, 10025).
    missing = list(hist)
    for x in inc:
        missing.remove(x)
    # Gaps 10016 (Capparella's raise) and 10025 (sefk's own fold) are absent
    # from the fixtures, so those two actions only appear in the history.
    assert missing == [(EventKind.RAISE, "Capparella"), (EventKind.FOLD, "sefk")]
    assert [b.sequence for b in seq if b.sequence in (10016, 10025)] == []


@pytest.mark.parametrize(
    "text,kind",
    [
        ("RiverRat is back from vacation", EventKind.BACK_FROM_VACATION),
        ("Hand over, no showdown", EventKind.HAND_OVER),
        ("Current board is:  ? ? ? ? ?", EventKind.SHOWDOWN_BOARD),
    ],
)
def test_no_showdown_lines(text, kind):
    from poker.parsing.broadcast import parse_event

    ev = parse_event(text)
    assert ev.kind == kind
    if kind == EventKind.BACK_FROM_VACATION:
        assert ev.player == "RiverRat"
    if kind == EventKind.SHOWDOWN_BOARD:
        assert ev.cards == ()


def test_all_in_suffix_keeps_action_kind():
    from poker.parsing.broadcast import parse_event

    ev = parse_event("sefk bets $63,500 and is all in")
    assert (ev.kind, ev.player, ev.amount, ev.all_in) == (EventKind.BET, "sefk", 63500, True)
    assert ev.text == "sefk bets $63,500 and is all in"
    ev = parse_event("Jim Anderson calls and is all in")
    assert (ev.kind, ev.all_in) == (EventKind.CALL, True)


def test_player_leaving_table():
    from poker.parsing.broadcast import parse_event

    ev = parse_event("Player leaving table: Andy Bloch")
    assert (ev.kind, ev.player) == (EventKind.PLAYER_LEFT, "Andy Bloch")


TALK_BODY = """! Table b17, Hand 4, Day 4

! Capparella folds
_____________________________________________________________________________

% Sorry about the delays. My BET, RAISE, and CALL commands are not registe
! -- RiverRat --
_____________________________________________________________________________

! RiverRat folds
! sefk is next to act, $200 to call
! Pot size: $300
! Betting round: 1
! Board: ? ? ? ? ?
"""


def test_table_talk_attaches_to_signature():
    b = parse_broadcast("[b17.10042][hand:4] Holdem Game Broadcast Message(-> sefk)", TALK_BODY)
    kinds = [e.kind for e in b.events]
    assert kinds == [EventKind.FOLD, EventKind.TABLE_TALK, EventKind.FOLD]
    talk = b.events[1]
    assert talk.player == "RiverRat"
    assert talk.text.startswith("Sorry about the delays.")
    assert b.next_to_act == "sefk" and b.to_call == 200


HISTORY_TALK_BODY = """! Table b17, Hand 4, Day 4

! RiverRat folds
! sefk is next to act, $200 to call
! History of this hand:
! 10/15/26 13:29:26! Aardvark is on vacation and folds
_____________________________________________________________________________

! Sorry about the delays. My BET, RAISE, and CALL commands are not registe
! -- RiverRat --
_____________________________________________________________________________

! 10/15/26 13:29:26! RiverRat folds
"""


def test_history_table_talk_is_one_event():
    # The history quotes talk with "!" rather than "%"; it must not become an OTHER line.
    b = parse_broadcast("[b17.10043][hand:4] Holdem Game Broadcast Message(-> sefk)", HISTORY_TALK_BODY)
    kinds = [e.kind for e in b.history]
    assert kinds == [EventKind.VACATION_FOLD, EventKind.TABLE_TALK, EventKind.FOLD]
    talk = b.history[1]
    assert talk.player == "RiverRat"
    assert talk.text.startswith("Sorry about the delays.")


def test_all_in_raise_without_total_keeps_actor():
    from poker.parsing.broadcast import parse_event

    ev = parse_event("X4 raises $4950 and is all in")
    assert (ev.kind, ev.player, ev.amount) == (EventKind.ALL_IN, "X4", 4950)
    ev = parse_event("X1 calls - side pot and is all in")
    assert (ev.kind, ev.player) == (EventKind.ALL_IN, "X1")


def test_status_url_must_be_http():
    body = TALK_BODY + "! The current status can be found at javascript:alert(1)\n"
    b = parse_broadcast("[b17.10042][hand:4] Holdem Game Broadcast Message(-> sefk)", body)
    assert b.status_url is None
