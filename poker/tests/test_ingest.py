from pathlib import Path

import pytest

from poker import state
from poker.models import RawEmail

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.django_db


def test_failed_apply_leaves_no_raw_email_so_the_poller_retries(monkeypatch):
    data = next(FIXTURES.rglob("*.eml")).read_bytes()

    def boom(raw, parsed):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(state, "apply", boom)
    with pytest.raises(RuntimeError):
        state.ingest(data, "gmail", gmail_id="G1")
    assert not RawEmail.objects.exists()


def test_emails_without_message_id_are_keyed_by_content():
    a = b"Subject: hi\r\n\r\nbody one\r\n"
    b = b"Subject: hi\r\n\r\nbody two\r\n"
    ra, _, _ = state.ingest(a, "eml")
    rb, _, _ = state.ingest(b, "eml")
    again, created, _ = state.ingest(a, "eml")
    assert ra.message_id.startswith("sha256:") and ra.pk != rb.pk
    assert again.pk == ra.pk and not created
