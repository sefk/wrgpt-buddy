import pytest
from django.urls import reverse

from poker.bankroll import bankroll_series, current_bankroll
from poker.models import Hand, Player, Seat, Table

pytestmark = pytest.mark.django_db


def _hand(table, number, start, end, when, me="sefk", over=True):
    h = Hand.objects.create(table=table, number=number, is_over=over, started_at=when)
    Seat.objects.create(
        hand=h, player=Player.objects.get_or_create(name=me)[0], seat_number=1,
        bankroll_start=start, bankroll_end=end,
    )
    return h


@pytest.fixture
def tournaments():
    from datetime import datetime, timezone

    def at(d):
        return datetime(2026, 9, d, tzinfo=timezone.utc)

    a, b, c = (Table.objects.create(name=n) for n in ("a1", "a2", "b1"))
    _hand(a, 1, 60000, 59800, at(1))
    _hand(a, 2, 59800, 57300, at(2))
    _hand(b, 1, 57300, 58000, at(3))   # table move: carries over
    _hand(c, 1, 60000, 60500, at(20))  # fresh stack: new tournament


def test_splits_tournaments_and_marks_table_changes(tournaments):
    s = bankroll_series("sefk")
    assert [t["tables"] for t in s] == [["a1", "a2"], ["b1"]]
    assert [p["table_change"] for p in s[0]["points"]] == [False, False, True]
    assert s[0]["current"] == 58000
    assert current_bankroll(s) == 60500


def test_missing_end_uses_next_start():
    from datetime import datetime, timezone

    t = Table.objects.create(name="x")
    _hand(t, 1, 60000, None, datetime(2026, 9, 1, tzinfo=timezone.utc))
    _hand(t, 2, 59000, 59500, datetime(2026, 9, 2, tzinfo=timezone.utc))
    assert [p["after"] for p in bankroll_series("sefk")[0]["points"]] == [59000, 59500]


def test_no_data():
    assert bankroll_series("sefk") == []
    assert current_bankroll() is None


def test_pages(tournaments, client, settings):
    settings.WRGPT_PLAYER_NAME = "sefk"
    r = client.get(reverse("poker:analytics"))
    assert r.status_code == 200 and b"bankroll-data" in r.content
    r = client.get(reverse("poker:hands"))
    assert b"Bankroll $60,500" in r.content


def test_analytics_empty(client):
    assert client.get(reverse("poker:analytics")).status_code == 200
