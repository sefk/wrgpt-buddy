import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from poker import gmail, pollstatus

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def status_file(settings, tmp_path):
    settings.POLL_STATUS_FILE = tmp_path / ".poll-status"


def test_healthy_when_never_polled_or_ok():
    assert pollstatus.problem() is None
    pollstatus.record_ok()
    assert pollstatus.problem() is None


def test_auth_failure_shows_immediately_and_ok_clears_it():
    pollstatus.record_failure("token expired", auth=True)
    assert pollstatus.problem() == {"error": "token expired", "auth": True}
    pollstatus.record_ok()
    assert pollstatus.problem() is None


def test_other_failures_show_only_once_persistent():
    for _ in range(pollstatus.BANNER_AFTER_FAILURES - 1):
        pollstatus.record_failure("dns")
        assert pollstatus.problem() is None
    pollstatus.record_failure("dns")
    assert pollstatus.problem()["auth"] is False


def test_poll_command_records_auth_failure_and_banner_renders(monkeypatch, client):
    def boom():
        raise gmail.GmailAuthError("token refresh failed; run: manage.py gmail_auth")

    monkeypatch.setattr(gmail, "build_service", boom)
    with pytest.raises(CommandError):
        call_command("poll_gmail")
    body = client.get("/").content.decode()
    assert "New hands aren't arriving" in body and "gmail_auth" in body


def test_poll_command_records_success(monkeypatch):
    monkeypatch.setattr(gmail, "build_service", lambda: object())
    monkeypatch.setattr("poker.management.commands.poll_gmail.poll", lambda *a, **k: (0, 0))
    pollstatus.record_failure("x", auth=True)
    call_command("poll_gmail")
    assert pollstatus.problem() is None
