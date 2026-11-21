"""My bankroll over time, split into tournaments, with table-change events."""

from __future__ import annotations

from datetime import datetime, timezone

from django.conf import settings

from .models import Action, Hand, StandingsSnapshot

# Hands in the rolling window for the "seen the flop" and "won" charts.
ROLLING_HANDS = 10

# A table change whose first bankroll is within this fraction of the previous
# hand's ending bankroll is a table move in the same tournament; anything else
# (e.g. a fresh $60,000 stack) starts a new one. Blinds shift it by a few percent.
SAME_TOURNAMENT_TOLERANCE = 0.01
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _friend_index(name: str) -> int | None:
    """Position of `name` in WRGPT_FRIENDS (case-insensitive), or None."""
    low = name.lower()
    for i, f in enumerate(settings.WRGPT_FRIENDS):
        if f.lower() == low:
            return i
    return None


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

    outcomes = _outcomes([h for h, _ in mine], me)
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
                **outcomes[h.pk],
            })
            last_table = h.table_id
        _add_play_stats(points)
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
            "friends": list(settings.WRGPT_FRIENDS),
        })
    return out


def _outcomes(hands, me: str) -> dict[int, dict]:
    """{hand_id: {flop, won}} for hands I was dealt into.

    `flop` is True when the flop came and I had not folded before it (betting round 1 is
    preflop), False when I folded preflop or the hand ended before a flop; `won` is True when
    the hand's results include a win for me. Both are None for a hand still being played.
    """
    ids = [h.pk for h in hands]
    folded, won = set(), set()
    for hand_id, kind, rnd in Action.objects.filter(
        hand_id__in=ids, player__name=me, kind__in=["fold", "vacation_fold", "wins"]
    ).values_list("hand_id", "kind", "betting_round"):
        if kind == "wins":
            won.add(hand_id)
        elif rnd is not None and rnd <= 1:
            folded.add(hand_id)
    out = {}
    for h in hands:
        if not h.is_over:
            out[h.pk] = {"flop": None, "won": None}
        else:
            out[h.pk] = {"flop": bool(h.board) and h.pk not in folded, "won": h.pk in won}
    return out


def _add_play_stats(points: list[dict]) -> None:
    """Add rolling (last ROLLING_HANDS) and cumulative flop%/win% to each finished hand's point.

    flop%: share of hands dealt that I saw the flop in. win%: share of the hands I saw the
    flop in that I won. Each is None until there is a hand to measure.
    """
    dealt, played = [], []  # flop flags of all finished hands; won flags of hands with a flop
    for p in points:
        stats = {"roll_flop": None, "cum_flop": None, "roll_win": None, "cum_win": None}
        if p["flop"] is not None:
            dealt.append(p["flop"])
            if p["flop"]:
                played.append(p["won"])
            stats["cum_flop"] = _pc(dealt)
            stats["roll_flop"] = _pc(dealt[-ROLLING_HANDS:])
            if played:
                stats["cum_win"] = _pc(played)
                stats["roll_win"] = _pc(played[-ROLLING_HANDS:])
        p.update(stats)


def _pc(flags: list[bool]) -> float:
    return round(100 * sum(flags) / len(flags), 1)


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
        "out_at": r.get("out_at", ""), "field": True, "friend": _friend_index(r["player"]),
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
            # My friends' standing in this snapshot; stack is None once they are out.
            "friends": [
                {"i": i, "player": r["player"], "rank": r["rank"],
                 "stack": None if r["out"] else r["stack"]}
                for r in snap.rows if (i := _friend_index(r["player"])) is not None
            ],
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
            "me": name == me, "out": v == 0, "friend": _friend_index(name),
        })
    return out


def _end(hs):
    h, seat = hs
    return seat.bankroll_end if seat.bankroll_end is not None else seat.bankroll_start


def current_bankroll(series: list[dict] | None = None) -> int | None:
    series = bankroll_series() if series is None else series
    return series[-1]["current"] if series else None
