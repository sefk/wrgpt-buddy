from pathlib import Path

import pytest
from django.core.management import call_command
from django.urls import reverse

from poker.models import Hand

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.django_db


@pytest.fixture
def imported():
    call_command("import_eml", str(FIXTURES))


def h(table, number):
    return Hand.objects.get(table__name=table, number=number)


def test_pages_200(imported, client):
    assert client.get("/").status_code == 200
    assert client.get("/hands/").status_code == 200
    r = client.get(reverse("poker:hand", args=["b17", 2]))
    assert r.status_code == 200
    assert b"Capparella" in r.content
    assert b"Request analysis" in r.content


def test_unknown_hand_404(imported, client):
    assert client.get("/hands/b17/999/").status_code == 404
    assert client.get("/hands/zzz/1/").status_code == 404


def test_empty_db(client):
    assert client.get("/").status_code == 200
    assert client.get("/hands/").status_code == 200


def test_current_prefers_live_hand(imported, client):
    Hand.objects.update(is_over=True)
    h1 = h("b17", 1)
    h1.is_over = False
    h1.last_sequence = 10**6
    h1.save()
    r = client.get("/")
    assert r.context["hand"].pk == h1.pk


def test_current_falls_back_to_most_recent_when_all_over(imported, client):
    Hand.objects.update(is_over=True)
    r = client.get("/")
    assert r.status_code == 200
    assert r.context["hand"].is_over


def test_my_cards_shown(imported, client):
    r = client.get(reverse("poker:hand", args=["b17", 2]))
    mine = h("b17", 2).mine
    assert mine.hole_cards
    assert r.content.decode().count('class="card ') >= 2 + 5  # hole + board
    assert "9" in mine.hole_cards


def test_missing_broadcast_warning(imported, client):
    hand = h("b17", 2)
    assert hand.missing_sequences()
    hand.is_over = False
    hand.has_history = False
    hand.save()
    r = client.get(reverse("poker:hand", args=["b17", 2]))
    assert b"never arrived" in r.content
    hand.is_over = True
    hand.save()
    r = client.get(reverse("poker:hand", args=["b17", 2]))
    assert b"never arrived" not in r.content


def test_history_groups_by_table(imported, client):
    r = client.get("/hands/")
    names = [t.name for t, _ in r.context["groups"]]
    assert "b17" in names and "a38" in names


def test_only_current_hand_is_live(imported, client):
    # a38 hand 2 is a stale practice hand; make b17 hand 2 the live one.
    for hand, over in ((h("a38", 2), False), (h("b17", 2), False)):
        hand.is_over = over
        hand.save()
    body = client.get("/hands/").content.decode()
    assert body.count(">live<") == 1
    assert ">unfinished<" in body
    # Most recently active table (b17) is listed first.
    assert body.index("b17") < body.index("a38")
    stale = client.get("/hands/a38/2/").content.decode()
    assert "Unfinished" in stale and "In progress" not in stale


def test_hand_zero_seating_phase_hidden(imported, client):
    from poker.models import Table

    Hand.objects.create(table=Table.objects.get(name="b17"), number=0, last_sequence=99999)
    assert "#0<" not in client.get("/hands/").content.decode()
    assert client.get("/").context["hand"].number != 0
