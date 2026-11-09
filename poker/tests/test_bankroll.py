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


def test_field_percentiles_and_ranking():
    from datetime import datetime, timezone

    t = Table.objects.create(name="r1")
    h = _hand(t, 1, 60000, 70000, datetime(2026, 9, 1, tzinfo=timezone.utc))
    for i, (name, end) in enumerate([("ann", 50000), ("bob", 40000), ("cy", 0)], 2):
        Seat.objects.create(hand=h, player=Player.objects.get_or_create(name=name)[0],
                            seat_number=i, bankroll_start=60000, bankroll_end=end)
    s = bankroll_series("sefk")[0]
    # cy busted (0) and is left out of the field.
    assert s["points"][0]["field"] == {"n": 3, "mean": 53333, "p25": 45000, "p50": 50000, "p75": 60000}
    assert [(r["rank"], r["player"], r["me"], r["out"]) for r in s["ranking"]] == [
        (1, "sefk", True, False), (2, "ann", False, False), (3, "bob", False, False), (4, "cy", False, True),
    ]


def test_ranking_uses_latest_hand(tournaments):
    s = bankroll_series("sefk")[0]
    assert [(r["player"], r["stack"], r["table"]) for r in s["ranking"]] == [("sefk", 58000, "a2")]


def test_ranking_prefers_standings_snapshot(tournaments):
    from poker import standings

    page = """<p>Players Remaining: 2 (50%)<br>Eliminated: 1
    <tr align='center'><td>1</td><td align='left'>ann</td><td>90000</td><td><a href="x">b1</a></td><td>0</td><td>in</td><td>1</td><td>1</td><td>&nbsp;</td></tr>
    <tr align='center'><td>2</td><td align='left'>sefk</td><td>50000</td><td><a href="x">b2</a></td><td>0</td><td>folded</td><td>1</td><td>1</td><td>&nbsp;</td></tr>
    <tr><td colspan='2'>Eliminated Players</td><td>h</td></tr>
    <tr><td align='center'>3</td><td>bob</td><td colspan='2'><a href="x">b9 - 3</a></td><td colspan='2'>Resigned</td><td colspan='3'>2026-09-21 06:00:00</td></tr>"""
    parsed = standings.parse(page)
    assert (parsed["remaining"], parsed["eliminated"], len(parsed["rows"])) == (2, 1, 3)
    assert parsed["rows"][2]["out"] and parsed["rows"][2]["out_where"] == "b9 - 3"
    snap = standings.scrape(page=page)
    # Snapshot is taken "now", after every fixture tournament; it belongs to the last one.
    s = bankroll_series("sefk")
    assert snap and s[0]["ranking"] != s[1]["ranking"]
    assert [(r["rank"], r["player"], r["me"], r["out"]) for r in s[-1]["ranking"]] == [
        (1, "ann", False, False), (2, "sefk", True, False), (3, "bob", False, True)]
    # One point per snapshot: live stacks only (bob is out), with my rank.
    assert s[-1]["tournament_field"] == [{
        "time": snap.fetched_at.isoformat(), "n": 2, "eliminated": 1, "mean": 70000,
        "p25": 60000, "p50": 70000, "p75": 80000, "min": 50000, "max": 90000,
        "my_rank": 2, "my_stack": 50000}]
    assert s[0]["tournament_field"] == []


def test_scrape_keeps_one_snapshot_per_day(tournaments):
    from poker import standings
    from poker.models import StandingsSnapshot

    page = ("<p>Players Remaining: 1<br>Eliminated: 0 <tr align='center'><td>1</td><td align='left'>ann</td>"
            "<td>90000</td><td>b1</td><td>0</td><td>in</td><td>1</td><td>1</td><td>&nbsp;</td></tr>")
    standings.scrape(page=page)
    last = standings.scrape(page=page)
    assert list(StandingsSnapshot.objects.values_list("pk", flat=True)) == [last.pk]
