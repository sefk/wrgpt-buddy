import base64
from pathlib import Path

import pytest
from django.core.management import call_command

from poker.management.commands.poll_gmail import poll
from poker.models import Hand, RawEmail

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.django_db


class _Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeGmail:
    """Just enough of the googleapiclient chain: users().messages().list/get()."""

    def __init__(self, files, page_size=10):
        # Gmail lists newest first.
        self.msgs = {f"g{i}": f.read_bytes() for i, f in enumerate(files)}
        self.order = list(reversed(self.msgs))
        self.page_size = page_size
        self.fetched = []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, pageToken=None, maxResults=500):
        start = int(pageToken or 0)
        ids = self.order[start : start + self.page_size]
        resp = {"messages": [{"id": i} for i in ids]}
        if start + self.page_size < len(self.order):
            resp["nextPageToken"] = str(start + self.page_size)
        return _Call(resp)

    def get(self, userId, id, format):
        self.fetched.append(id)
        return _Call({"raw": base64.urlsafe_b64encode(self.msgs[id]).decode()})


def test_poll_ingests_everything_across_pages():
    files = sorted(FIXTURES.rglob("*.eml"))
    svc = FakeGmail(files, page_size=7)
    new, bad = poll(svc, "q")
    assert (new, bad) == (len(files), 0)
    assert RawEmail.objects.filter(source="gmail").count() == len(files)
    h = Hand.objects.get(table__name="b17", number=2)
    assert h.is_over and "Capparella" in h.winner


def test_poll_skips_known_and_matches_eml_imports():
    files = sorted(FIXTURES.rglob("*.eml"))
    call_command("import_eml", str(FIXTURES / "hand_1"))
    svc = FakeGmail(files)
    new, _ = poll(svc, "q")
    assert new == len(files) - len(list((FIXTURES / "hand_1").glob("*.eml")))
    # eml-imported messages get their gmail_id filled in, so a second poll fetches nothing
    assert not RawEmail.objects.filter(gmail_id=None).exists()
    svc.fetched.clear()
    assert poll(svc, "q") == (0, 0)
    assert svc.fetched == []
