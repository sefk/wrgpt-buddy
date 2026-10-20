"""My bankroll over time, split into tournaments, with table-change events."""

from __future__ import annotations

from datetime import datetime, timezone

from django.conf import settings

from .models import Hand

# A table change whose first bankroll is within this fraction of the previous
# hand's ending bankroll is a table move in the same tournament; anything else
# (e.g. a fresh $60,000 stack) starts a new one. Blinds shift it by a few percent.
SAME_TOURNAMENT_TOLERANCE = 0.01
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _when(hand):
    return hand.started_at or hand.ended_at


def bankroll_series(me: str | None = None) -> list[dict]:
    """[{index, label, started, ended, current, tournament: [point]}], oldest first.

    A point is {hand_id, table, number, time, before, after, table_change}: `before`
    is my bankroll at the start of the hand (after blinds), `after` at the end.
    A hand with no ending bankroll uses the next hand's start in the same tournament,
    then my current bankroll if it is still being played, then its start.
    """
    me = me or settings.WRGPT_PLAYER_NAME
    hands = list(
        Hand.objects.dealt().select_related("table").prefetch_related("seats__player")
    )
    mine = []
    for h in hands:
        seat = next((s for s in h.seats.all() if s.player.name == me), None)
        if seat and seat.bankroll_start is not None:
            mine.append((h, seat))

    first_seen = {}
    for h, _ in mine:
        t = _when(h)
        if t and (h.table_id not in first_seen or t < first_seen[h.table_id]):
            first_seen[h.table_id] = t
    mine.sort(key=lambda hs: (first_seen.get(hs[0].table_id, _EPOCH), hs[0].table.name, hs[0].number))

    # Split into tournaments at table changes where the bankroll doesn't carry over.
    runs, prev = [], None
    for h, seat in mine:
        if prev is not None and h.table_id == prev[0].table_id:
            runs[-1].append((h, seat))
        else:
            carried = prev is not None and abs(seat.bankroll_start - _end(prev)) <= (
                SAME_TOURNAMENT_TOLERANCE * max(_end(prev), 1))
            if carried:
                runs[-1].append((h, seat))
            else:
                runs.append([(h, seat)])
        prev = (h, seat)

    out = []
    for i, run in enumerate(runs, 1):
        points, last_time, last_table = [], None, None
        for j, (h, seat) in enumerate(run):
            nxt = run[j + 1][1].bankroll_start if j + 1 < len(run) else None
            if seat.bankroll_end is not None:
                after = seat.bankroll_end
            elif nxt is not None:
                after = nxt
            elif not h.is_over and seat.bankroll_now is not None:
                after = seat.bankroll_now
            else:
                after = seat.bankroll_start
            t = _when(h) or last_time
            last_time = t or last_time
            points.append({
                "hand_id": h.pk, "table": h.table.name, "number": h.number,
                "time": t.isoformat() if t else None,
                "before": seat.bankroll_start, "after": after,
                "table_change": last_table is not None and h.table_id != last_table,
            })
            last_table = h.table_id
        times = [p["time"] for p in points if p["time"]]
        out.append({
            "index": i,
            "label": f"Tournament {i}",
            "tables": list(dict.fromkeys(p["table"] for p in points)),
            "started": times[0] if times else None,
            "ended": times[-1] if times else None,
            "current": points[-1]["after"],
            "points": points,
        })
    return out


def _end(hs):
    h, seat = hs
    return seat.bankroll_end if seat.bankroll_end is not None else seat.bankroll_start


def current_bankroll(series: list[dict] | None = None) -> int | None:
    series = bankroll_series() if series is None else series
    return series[-1]["current"] if series else None
