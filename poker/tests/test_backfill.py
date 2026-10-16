from pathlib import Path

import pytest
from django.core.management import call_command

from poker.advice import engine
from poker.advice.points import my_decisions
from poker.models import Hand, Recommendation

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.django_db


@pytest.fixture
def hand():
    call_command("import_eml", str(FIXTURES))
    h = Hand.objects.get(table__name="b17", number=2)
    assert h.is_over and my_decisions(h)
    return h


def test_backfill_queues_missing_points_once(hand):
    points = [d.point for d in my_decisions(hand)]
    Recommendation.objects.create(hand=hand, point=points[0], decision_key="x", status=Recommendation.DONE)
    year = (hand.started_at or hand.ended_at).year
    call_command("backfill_analyses", "--year", str(year))
    queued = set(hand.recommendations.filter(decision_key__startswith="backfill-").values_list("point", flat=True))
    assert queued == set(points[1:])
    call_command("backfill_analyses", "--year", str(year))
    assert hand.recommendations.filter(decision_key__startswith="backfill-").count() == len(points) - 1


def test_backfill_skips_other_years(hand):
    call_command("backfill_analyses", "--year", "1999")
    assert not Recommendation.objects.exists()


def test_queue_runs_backfill_after_newer_live_work(hand):
    old = Recommendation.objects.create(hand=hand, point="r1n1", decision_key="backfill-r1n1")
    new = Recommendation.objects.create(hand=hand, point="", decision_key="seq1")
    assert list(engine.queue()) == [new, old]


def test_backfill_requeues_errored_points(hand):
    year = str((hand.started_at or hand.ended_at).year)
    call_command("backfill_analyses", "--year", year)
    hand.recommendations.update(status=Recommendation.ERROR)
    call_command("backfill_analyses", "--year", year)
    assert hand.recommendations.filter(status=Recommendation.PENDING).count() == len(my_decisions(hand))


def test_backfill_redo_requeues_done_points_but_not_queued_ones(hand):
    year = str((hand.started_at or hand.ended_at).year)
    points = [d.point for d in my_decisions(hand)]
    Recommendation.objects.create(hand=hand, point=points[0], decision_key="x", status=Recommendation.DONE)
    Recommendation.objects.create(hand=hand, point=points[1], decision_key="y", status=Recommendation.PENDING)
    call_command("backfill_analyses", "--year", year, "--redo")
    queued = list(hand.recommendations.filter(decision_key__startswith="backfill-").values_list("point", flat=True))
    assert queued == [points[0]]
