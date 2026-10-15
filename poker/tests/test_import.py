from pathlib import Path

import pytest
from django.core.management import call_command

from poker.models import Hand, MyHand, RawEmail

pytest.importorskip("poker.parsing.broadcast")

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.django_db


def test_import_all_fixtures():
    call_command("import_eml", str(FIXTURES))
    assert RawEmail.objects.count() == len(list(FIXTURES.rglob("*.eml")))

    h = Hand.objects.get(table__name="b17", number=2)
    assert h.is_over
    assert "Capparella" in h.winner
    assert h.board == "As 6c Th 4c 2h"
    actions = list(h.actions.all())
    assert 28 <= len(actions) <= 40
    # showdown lines in the dealer's history carry no timestamp
    untimed = {a.kind for a in actions if not a.timestamp}
    assert untimed <= {"showdown_board", "shows", "wins"}
    assert sum(1 for a in actions if a.timestamp) >= 30
    assert {a.betting_round for a in actions} == {1, 2, 3, 4}
    assert h.started_at < h.ended_at

    mine = MyHand.objects.get(hand=h)
    assert mine.hole_cards == "9d 9s"
    assert mine.last_command

    assert MyHand.objects.filter(hand__table__name="a38").exists()


def test_import_is_idempotent_and_reparse():
    call_command("import_eml", str(FIXTURES))
    n_actions = Hand.objects.get(table__name="b17", number=2).actions.count()
    call_command("import_eml", str(FIXTURES))
    assert RawEmail.objects.count() == len(list(FIXTURES.rglob("*.eml")))
    assert Hand.objects.get(table__name="b17", number=2).actions.count() == n_actions
    call_command("reparse")
    assert Hand.objects.get(table__name="b17", number=2).actions.count() == n_actions


def test_hand_1_no_showdown():
    call_command("import_eml", str(FIXTURES / "hand_1"))
    h = Hand.objects.get(table__name="b17", number=1)
    assert h.is_over
    assert h.winner == "sefk wins $700 (net $300)"
    assert not h.actions.filter(kind="other").exists()
    assert h.actions.filter(kind="back_from_vacation").count() == 8
    assert h.missing_sequences() == []
    mine = MyHand.objects.get(hand=h)
    assert mine.hole_cards == "Jc Ad"
    assert mine.last_command == "bet $400"


def test_missing_broadcasts_detected():
    call_command("import_eml", str(FIXTURES))
    assert Hand.objects.get(table__name="b17", number=1).missing_sequences() == []
    assert Hand.objects.get(table__name="b17", number=2).missing_sequences() == [10016, 10025]


def test_gap_before_first_broadcast_of_hand(tmp_path):
    # Without hand 1 we can't know where hand 2 started; with it, a lost
    # first broadcast of hand 2 shows up as a gap.
    for f in (FIXTURES / "hand_1").glob("*.eml"):
        call_command("import_eml", str(f))
    for f in (FIXTURES / "hand_2").glob("*.eml"):
        if "10014" not in f.name:
            call_command("import_eml", str(f))
    assert Hand.objects.get(table__name="b17", number=2).missing_sequences()[0] == 10014
