"""Scrape the tournament-wide standings from wrgpt.org."""

from __future__ import annotations

import html
import re
import urllib.request
from datetime import timedelta

from django.utils import timezone

from .models import StandingsSnapshot

# http: the server omits its intermediate TLS certificate, which Python (unlike curl) rejects.
# The page is public and read-only, so nothing is lost.
URL = "http://www.wrgpt.org/wrgpt_standings.php"
MIN_INTERVAL = timedelta(minutes=30)

_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def _text(s: str) -> str:
    return html.unescape(_TAG.sub("", s)).replace("\xa0", " ").strip()


def parse(page: str) -> dict:
    """{remaining, eliminated, rows}; raises ValueError if the page has no standings."""
    m = re.search(r"Players Remaining:\s*(\d+).*?Eliminated:\s*(\d+)", page, re.S)
    rows = []
    for raw in _ROW.findall(page):
        cells = [_text(c) for c in _CELL.findall(raw)]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        if len(cells) >= 8:  # live: # player bankroll table in-pot status max min old
            rows.append({"rank": int(cells[0]), "player": cells[1], "stack": int(cells[2]),
                         "table": cells[3], "status": cells[5], "out": False})
        else:  # eliminated: # player "table - hand" "cards or Resigned" "date time"
            rows.append({"rank": int(cells[0]), "player": cells[1], "stack": 0,
                         "table": "", "status": "out", "out": True,
                         "out_where": cells[2] if len(cells) > 2 else "",
                         "out_at": cells[4] if len(cells) > 4 else ""})
    if not rows:
        raise ValueError("no standings rows found")
    live = sum(1 for r in rows if not r["out"])
    return {"remaining": int(m.group(1)) if m else live,
            "eliminated": int(m.group(2)) if m else len(rows) - live, "rows": rows}


def fetch(url: str = URL) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "wrgpt-buddy"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def scrape(force: bool = False, page: str | None = None) -> StandingsSnapshot | None:
    """Store a new snapshot unless one was taken in the last MIN_INTERVAL (or force)."""
    last = StandingsSnapshot.objects.first()
    if not force and page is None and last and timezone.now() - last.fetched_at < MIN_INTERVAL:
        return None
    data = parse(page if page is not None else fetch())
    return StandingsSnapshot.objects.create(**data)
