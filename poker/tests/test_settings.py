from django.conf import settings


def test_sqlite_waits_for_write_lock():
    # Poll, web and the recommend worker share one SQLite file; the 5 s default
    # produced "database is locked" ingest failures (docs/log-review-2026-11-25.md).
    assert settings.DATABASES["default"]["OPTIONS"]["timeout"] >= 30
