from pathlib import Path

import pytest
from django.core.management import call_command

from poker.advice import replay as rp
from poker.advice.prompt import build_prompt
from poker.models import Hand

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.django_db


@pytest.fixture
def h2():
    call_command("import_eml", str(FIXTURES / "hand_1"), str(FIXTURES / "hand_2"))
    return Hand.objects.get(table__name="b17", number=2)


def test_preflop_call(h2):
    r = rp.replay(h2, "r1n1")
    assert r.street == "preflop" and r.board == []
    assert r.current_bet == 650 and r.my_committed == 0 and r.to_call == 650
    # blinds 100+200, Capparella 650, RiverRat 650
    assert r.pot == 1600
    assert r.my_stack == 60300
    assert r.stacks["Two_Jacks"] == 59700 and r.stacks["Michael Rankin"] == 59800
    assert r.in_hand["Mark Ballinger"] is False and r.in_hand["Options Dee"] is False
    assert r.in_hand["Michael Rankin"] and r.in_hand["Bryan Currie"]
    assert r.dealer == "Two_Jacks" and r.hole_cards == "9d 9s"
    assert r.log[-1].text == "RiverRat calls"


def test_flop_fold(h2):
    r = rp.replay(h2, "r2n1")
    assert r.street == "flop" and r.board == ["As", "6c", "Th"]
    assert r.pot == 4650 and r.boundaries[-1][1] == 3450 and r.current_bet == 600 and r.to_call == 600 and r.my_committed == 0
    assert r.my_stack == 59650
    assert r.in_hand["RiverRat"] is False and r.in_hand["Capparella"]
    assert r.log[-1].text == "RiverRat folds"
    assert r.log[-1].betting_round == 2


def test_unknown_point(h2):
    with pytest.raises(ValueError):
        rp.replay(h2, "r4n1")


def test_street_boundary_pots_match_dealer(h2):
    assert [(d, o) for _, d, o in rp.validate(h2)] == [(3450, 3450), (5250, 5250), (7650, 7650)]


def test_stack_reconstruction_matches_final_bankrolls(h2):
    rows = rp.validate_stacks(h2)
    assert rows and all(a == b for _, a, b in rows)


def test_validate_command_all_hands_consistent(h2, capsys):
    call_command("validate_replay", "--all")
    out = capsys.readouterr().out
    assert "street pots" in out
    import re
    a, b = re.search(r"street pots (\d+)/(\d+)", out).groups()
    assert a == b and int(b) >= 3


def test_prompt_has_no_hindsight(h2):
    text, snap = build_prompt(h2, "r1n1")
    assert snap == {"cards": "9d 9s", "board": "", "pot": 1600, "to_call": 650,
                    "betting_round": "preflop", "next_to_act": "sefk", "my_stack": 60300}
    for future in ("4c", "2h", "Capparella wins", "has:", "sefk folds", "Pot right", "Flop", "Kh Jh",
                   "NoSho", "FOLDED  ", "$14,650", "59650"):
        assert future not in text, future
    assert "Amount to call: $650" in text and "Pot: $1,600" in text
    assert "Mark Ballinger  $60,000  [FOLDED]" in text
    # flop decision: sees the flop and the actions before it, nothing later
    text, snap = build_prompt(h2, "r2n1")
    assert snap["board"] == "As 6c Th" and snap["pot"] == 4650 and snap["to_call"] == 600
    for future in ("4c", "2h", "Capparella wins", "has:", "sefk folds", "Two_Jacks bets $800", "Kh Jh"):
        assert future not in text, future
    assert "Two_Jacks bets $600" in text and "RiverRat folds" in text
