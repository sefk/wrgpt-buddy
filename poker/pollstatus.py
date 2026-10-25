"""Outcome of the latest Gmail poll, shared between the poll job and the web app.

The poll job runs as its own process every few minutes; it records the result in a
small JSON file so every page can warn when new hands have stopped arriving.
"""

from __future__ import annotations

import json
import time

from django.conf import settings

# Non-auth failures (network blips) only warrant a banner once they persist.
BANNER_AFTER_FAILURES = 3


def _write(data: dict) -> None:
    try:
        settings.POLL_STATUS_FILE.write_text(json.dumps(data))
    except OSError:
        pass  # status is advisory; never fail a poll over it


def read() -> dict:
    try:
        return json.loads(settings.POLL_STATUS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def record_ok() -> None:
    _write({"ok": True, "last_ok": time.time(), "failures": 0})


def record_failure(error: str, auth: bool = False) -> None:
    prev = read()
    _write({"ok": False, "auth": auth, "error": error, "failures": prev.get("failures", 0) + 1,
            "last_ok": prev.get("last_ok"), "since": prev.get("since") if not prev.get("ok", True) else time.time()})


def problem() -> dict | None:
    """The warning to show on every page, or None when polling is healthy."""
    s = read()
    if s.get("ok", True):
        return None
    if not s.get("auth") and s.get("failures", 0) < BANNER_AFTER_FAILURES:
        return None
    return {"error": s.get("error", ""), "auth": bool(s.get("auth"))}
