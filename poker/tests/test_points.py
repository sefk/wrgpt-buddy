from pathlib import Path

import pytest
from django.core.management import call_command

from poker.advice.points import live_point, my_decisions
from poker.models import Hand, Player, Recommendation

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.django_db


@pytest.fixture
def imported():
    call_command("import_eml", str(FIXTURES / "hand_1"), str(FIXTURES / "hand_2"))


def test_my_decisions_hand_2(imported):
    pts = my_decisions(Hand.objects.get(table__name="b17", number=2))
    assert [(p.point, p.action.kind) for p in pts] == [("r1n1", "call"), ("r2n1", "fold")]


def test_live_point(imported):
    h = Hand.objects.get(table__name="b17", number=2)
    assert live_point(h) is None  # hand over
    h.is_over = False
    h.betting_round = 2
    h.next_to_act = Player.objects.get(name="sefk")
    assert live_point(h).point == "r2n2"


def test_recommendations_survive_reparse(imported):
    h = Hand.objects.get(table__name="b17", number=2)
    Recommendation.objects.create(hand=h, decision_key="pt-r1n1", point="r1n1", status="done")
    call_command("reparse")
    rec = Recommendation.objects.get(point="r1n1")
    assert rec.hand == Hand.objects.get(table__name="b17", number=2)
    assert (rec.table_name, rec.hand_number) == ("b17", 2)
