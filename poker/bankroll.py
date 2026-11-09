"""My bankroll over time, split into tournaments, with table-change events."""

from __future__ import annotations

from datetime import datetime, timezone

from django.conf import settings

from .models import Hand, StandingsSnapshot

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

    Each point also has `field`: {n, mean, p25, p50, p75} of the stacks of everyone
    still in at that table after the hand (me included), and each tournament has a
    `ranking` of every player (latest standings snapshot, else players seen at its tables)
    and a `tournament_field` series, one point per standings snapshot.
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

    snaps = list(StandingsSnapshot.objects.order_by("fetched_at"))
    starts = [min((t for t in (_when(h) for h, _ in run) if t), default=None) for run in runs]
    out = []
    for i, run in enumerate(runs, 1):
        tsnaps = _window(snaps, starts, i - 1)
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
                "field": _field(h, me, after),
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
            "ranking": _tournament_ranking(tsnaps, me)
            or _ranking(hands, {h.table_id for h, _ in run}, me),
            "tournament_field": _tournament_field(tsnaps, me),
        })
    return out


def _window(snaps, starts, i):
    """Snapshots taken during tournament i, oldest first."""
    lo = starts[i]
    hi = starts[i + 1] if i + 1 < len(starts) else None
    if lo is None:
        return []
    return [s for s in snaps if s.fetched_at >= lo and (hi is None or s.fetched_at < hi)]


def _tournament_ranking(snaps, me) -> list[dict]:
    """The latest standings snapshot (all players, all tables)."""
    if not snaps:
        return []
    snap = snaps[-1]
    return [{
        "rank": r["rank"], "player": r["player"], "stack": r["stack"], "table": r["table"],
        "number": None, "time": snap.fetched_at.isoformat(), "me": r["player"] == me,
        "out": r["out"], "status": r["status"], "out_where": r.get("out_where", ""),
        "out_at": r.get("out_at", ""), "field": True,
    } for r in snap.rows]


def _tournament_field(snaps, me) -> list[dict]:
    """Stack distribution of everyone still in, one point per snapshot."""
    out = []
    for snap in snaps:
        stacks = sorted(r["stack"] for r in snap.rows if not r["out"] and r["stack"])
        if not stacks:
            continue
        mine = next((r for r in snap.rows if r["player"] == me), None)
        out.append({
            "time": snap.fetched_at.isoformat(), "n": len(stacks), "eliminated": snap.eliminated,
            "mean": round(sum(stacks) / len(stacks)),
            "p25": round(_pct(stacks, 0.25)), "p50": round(_pct(stacks, 0.5)),
            "p75": round(_pct(stacks, 0.75)),
            "min": stacks[0], "max": stacks[-1],
            "my_rank": mine["rank"] if mine else None,
            "my_stack": mine["stack"] if mine and not mine["out"] else None,
        })
    return out


def _stack(seat) -> int | None:
    if seat.bankroll_end is not None:
        return seat.bankroll_end
    if seat.bankroll_now is not None:
        return seat.bankroll_now
    return seat.bankroll_start


def _pct(xs: list[int], q: float) -> float:
    """Linear-interpolated percentile of sorted xs (q in 0..1)."""
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _field(hand, me: str, my_after: int) -> dict | None:
    """Stack distribution at this table after the hand; busted players (0) are out."""
    stacks = []
    for s in hand.seats.all():
        v = my_after if s.player.name == me else _stack(s)
        if v:
            stacks.append(v)
    if not stacks:
        return None
    stacks.sort()
    return {
        "n": len(stacks),
        "mean": round(sum(stacks) / len(stacks)),
        "p25": round(_pct(stacks, 0.25)),
        "p50": round(_pct(stacks, 0.5)),
        "p75": round(_pct(stacks, 0.75)),
    }


def _ranking(hands, table_ids: set, me: str) -> list[dict]:
    """Every player seen at these tables, by the stack in their latest hand there.

    [{rank, player, stack, table, number, time, me, out}], biggest stack first;
    players whose latest stack is 0 are out and rank last.
    """
    latest = {}
    for h in hands:
        if h.table_id not in table_ids:
            continue
        key = (_when(h) or _EPOCH, h.number)
        for s in h.seats.all():
            v = _stack(s)
            if v is None:
                continue
            name = s.player.name
            if name not in latest or key >= latest[name][0]:
                latest[name] = (key, v, h)
    rows = sorted(latest.items(), key=lambda kv: (-kv[1][1], kv[0].lower()))
    out = []
    for i, (name, ((t, _), v, h)) in enumerate(rows, 1):
        out.append({
            "rank": i, "player": name, "stack": v, "table": h.table.name, "number": h.number,
            "time": t.isoformat() if t is not _EPOCH else None,
            "me": name == me, "out": v == 0,
        })
    return out


def _end(hs):
    h, seat = hs
    return seat.bankroll_end if seat.bankroll_end is not None else seat.bankroll_start


def current_bankroll(series: list[dict] | None = None) -> int | None:
    series = bankroll_series() if series is None else series
    return series[-1]["current"] if series else None
