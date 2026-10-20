import re
from datetime import datetime, timezone
from collections import OrderedDict

from django.conf import settings
from django.db.models import Max
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone as djtz
from django.views.decorators.http import require_POST

from .advice import engine, grading
from .bankroll import bankroll_series
from .advice.points import live_point, my_decisions
from .models import Action, Hand, MyHand, PlayerProfile, Recommendation
from .state import MIN_HANDS_FOR_BADGE, PROFILE_BLURBS

ROUND_NAMES = {1: "Preflop", 2: "Flop", 3: "Turn", 4: "River"}
SHOWS_RE = re.compile(r"^\s*(.+?)\s+has:\s*(.+)$")


def _me():
    return settings.WRGPT_PLAYER_NAME


def _with_activity(qs):
    return qs.annotate(activity=Max("actions__source__sent_at"))


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _recency(h):
    return (h.activity or _EPOCH, h.last_sequence, h.pk)


def current_hand():
    """Most recently active hand that isn't over; else the most recent hand."""
    hands = list(_with_activity(Hand.objects.dealt().select_related("table", "next_to_act")))
    newest = {}
    for h in hands:
        newest[h.table_id] = max(newest.get(h.table_id, 0), h.number)
    # A hand whose final broadcast never arrived stays not-over; a later hand on its
    # table proves it is finished.
    live = [h for h in hands if not h.is_over and h.number == newest[h.table_id]]
    pool = live or hands
    return max(pool, key=_recency) if pool else None


def _chips_in(hand, actions):
    """Best-effort chips committed per player this hand, from the action log."""
    commit = {}  # (player_id, round) -> amount
    for a in actions:
        if not a.player_id:
            continue
        r = a.betting_round or 1
        key = (a.player_id, r)
        if a.kind in ("blind", "bet") and a.amount is not None:
            commit[key] = a.amount if a.kind == "blind" else commit.get(key, 0) + a.amount
        elif a.kind == "raise" and a.total is not None:
            commit[key] = a.total
        elif a.kind == "call":
            commit[key] = max([v for (_, rr), v in commit.items() if rr == r], default=0)
        else:
            continue
    totals = {}
    for (pid, _), v in commit.items():
        totals[pid] = totals.get(pid, 0) + v
    return totals


def _shows(actions):
    out = {}
    for a in actions:
        if a.kind == "shows":
            m = SHOWS_RE.match(a.text)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def _last_nta(actions):
    """Fallback: scan the action log backwards for 'is next to act' text."""
    pat = re.compile(r"^(.+?)\s+is next to act")
    for a in reversed(actions):
        m = pat.match(a.text)
        if m:
            return m.group(1).strip()
    return None


def _detail_context(hand, open_point=""):
    me = _me()
    actions = list(hand.actions.select_related("player"))
    seats = list(hand.seats.select_related("player"))
    shows = _shows(actions)
    chips = _chips_in(hand, actions)
    mine = MyHand.objects.filter(hand=hand).first()
    nta = hand.next_to_act.name if hand.next_to_act else _last_nta(actions)

    # Build profiles for all opponents
    profiles = {}
    try:
        for p in PlayerProfile.objects.filter(table=hand.table).select_related("player"):
            profiles[p.player.name] = p
    except Exception:
        pass

    rows = []
    for s in seats:
        name = s.player.name
        delta = None
        if hand.is_over and s.bankroll_start is not None and s.bankroll_end is not None:
            delta = s.bankroll_end - s.bankroll_start
        profile = profiles.get(name)
        rows.append({
            "seat": s,
            "name": name,
            "is_me": name == me,
            "to_act": (not hand.is_over) and name == nta,
            "status": s.status.strip("<>") if s.status else "",
            "chips": chips.get(s.player_id),
            "delta": delta,
            "shown": shows.get(name),
            "profile": profile,
        })

    streets = OrderedDict()
    for a in actions:
        if a.kind in ("new_hand", "ante", "players_left", "showdown_board"):
            continue
        key = a.betting_round or 1
        streets.setdefault(key, []).append(a)
    street_list = [
        {"name": ROUND_NAMES.get(k, f"Round {k}"), "actions": v} for k, v in sorted(streets.items())
    ]

    missing = [] if hand.is_over or hand.has_history else hand.missing_sequences()
    my_turn = bool(not hand.is_over and nta == me)
    my_seat = next((r for r in rows if r["is_me"]), None)
    ctx = {
        "hand": hand,
        "round_name": ROUND_NAMES.get(hand.betting_round, ""),
        "mine": mine,
        "my_turn": my_turn,
        "me": me,
        "rows": rows,
        "my_seat": my_seat,
        "streets": street_list,
        "missing": missing,
        "next_to_act_name": nta,
        "any_shown": bool(shows),
    }
    by_action, live_entry, other, analyzing = _point_entries(hand, mine, open_point)
    shown = [a for st in street_list for a in st["actions"]]
    at_action, unknown = _place_by_time(other, shown)
    for st in street_list:
        st["actions"] = [
            {"a": a, "pt": by_action.get(a.pk), "notes": at_action.get(a.pk, [])} for a in st["actions"]
        ]
    ctx.update(live=live_entry, unknown=unknown, analyzing=analyzing)
    return ctx


KEY_SEQ_RE = re.compile(r"^(?:seq|req-)(\d+)")
GRADE_LABELS = {
    "agree": ("agree", "agree"),
    "same_action_different_size": ("different size", "size"),
    "different_action": ("different action", "action"),
}


def _norm(v):
    return " ".join(str(v).split()) if v not in (None, "") else ""


def _whole(v):
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _is_stale(rec, hand, mine):
    m = KEY_SEQ_RE.match(rec.decision_key)
    if m and int(m.group(1)) != hand.last_sequence:
        return True
    snap = rec.snapshot or {}
    current = {"board": hand.board, "pot": hand.pot, "cards": mine.hole_cards if mine else None}
    for k, cur in current.items():
        if k in snap and cur is not None and _norm(snap[k]) != _norm(cur):
            return True
    return False


def _headline(o):
    amount = o.get("amount")
    money = f"${_whole(amount):,}" if amount not in (None, "") else ""
    return " ".join(str(x) for x in (o.get("action"), money) if x)


def _rec_info(rec, hand, mine, past):
    """Everything the templates need to draw one Recommendation."""
    now = djtz.now()
    revealed = past or rec.revealed_at is not None
    info = {"obj": rec, "status": rec.status, "revealed": revealed}
    if rec.status in (Recommendation.PENDING, Recommendation.RUNNING):
        since = rec.started_at or rec.created_at
        info["minutes"] = max(0, int((now - since).total_seconds() // 60))
        info["steps"] = steps_for(rec, revealed)
        info["ahead"] = queue_ahead(rec)
        info["progress_url"] = reverse("poker:progress", args=[hand.table.name, hand.number, rec.pk])
    elif rec.status == Recommendation.DONE:
        opinions = [dict(o, amount=_whole(o.get("amount"))) for o in rec.opinions or []]
        info["n_opinions"] = len(opinions)
        if revealed:
            judgement = rec.judgement or {}
            verdicts = {v.get("model"): v for v in judgement.get("verdicts", [])}
            disagree = rec.concurrence not in ("agree", "")
            for o in opinions:
                label, css = GRADE_LABELS.get(o.get("grade"), ("", ""))
                o["grade_label"], o["grade_css"] = label, css
                o["headline"] = _headline(o)
                if disagree:
                    o["verdict"] = verdicts.get(o.get("label")) or verdicts.get(o.get("model"))
            actual = rec.actual or None
            if actual:
                actual = dict(actual, amount=_whole(actual.get("amount")), headline=_headline(actual))
            glabel, gcss = GRADE_LABELS.get(rec.actual_grade, ("", ""))
            info.update(
                opinions=opinions,
                judgement=judgement,
                disagree=disagree,
                all_answered=bool(opinions) and all(o.get("action") and not o.get("error") for o in opinions),
                concurrence=rec.concurrence,
                lead=next((o for o in opinions if o.get("label") == "Sonnet"), opinions[0] if opinions else None),
                stale=False if past else _is_stale(rec, hand, mine),
                snapshot=rec.snapshot or {},
                cache_read=judgement.get("cache_read_tokens"),
                actual=actual,
                actual_grade_label=glabel,
                actual_grade_css=gcss,
                on_actual=judgement.get("on_actual", ""),
            )
    return info


def _seq_of(rec):
    m = KEY_SEQ_RE.match(rec.decision_key)
    return int(m.group(1)) if m else None


def _older_entry(r, past):
    o = {"obj": r, "status": r.status, "revealed": past or r.revealed_at is not None}
    if r.status == Recommendation.DONE and r.opinions and o["revealed"]:
        o["headline"] = _headline(r.opinions[0])
    return o


def _point_entries(hand, mine, open_point=""):
    """Pair each of my decisions (and the live one) with its recommendations.

    Returns (by_action_pk, live_entry, other_entries, analyzing).
    """
    recs = list(hand.recommendations.all())  # newest first
    decisions = my_decisions(hand)
    live = live_point(hand)
    by_point = {}
    for r in recs:
        by_point.setdefault(r.point, []).append(r)
    live_name = live.point if live else None
    claimed = {d.point for d in decisions}
    if live_name:
        claimed.add(live_name)
        # legacy rows (blank point) for this very decision belong to the live row
        for r in by_point.pop("", []):
            seq = _seq_of(r)
            if seq is not None and seq == hand.last_sequence:
                by_point.setdefault(live_name, []).append(r)
            else:
                by_point.setdefault("", []).append(r)
        by_point[live_name] = sorted(by_point.get(live_name, []), key=lambda r: r.created_at, reverse=True)

    def entry(point, past, decision):
        rs = by_point.get(point, [])
        return {
            "point": point,
            "anchor": f"pt-{point}",
            "past": past,
            "decision": decision,
            "rec": _rec_info(rs[0], hand, mine, past) if rs else None,
            "older": [_older_entry(r, past) for r in rs[1:]],
            "open": point == open_point,
        }

    by_action = {d.action.pk: entry(d.point, True, d) for d in decisions}
    live_entry = None
    if live:
        live_entry = entry(live.point, False, live)
        live_entry["round_name"] = ROUND_NAMES.get(live.betting_round, f"Round {live.betting_round}")
        live_entry["anchor"] = "pt-live"
    other = []
    for point, rs in by_point.items():
        if point in claimed:
            continue
        for r in rs:
            o = _rec_info(r, hand, mine, True)
            o["label"] = r.point or r.decision_key
            other.append(o)
    other.sort(key=lambda o: o["obj"].created_at, reverse=True)
    analyzing = any(r.status in (Recommendation.PENDING, Recommendation.RUNNING) for r in recs)
    return by_action, live_entry, other, analyzing


def _place_by_time(entries, actions):
    """Attach analyses with no decision point to the last action logged before each ran.

    Returns ({action_pk: [entry, ...]}, unplaced) -- unplaced when no action is timestamped
    at or before the analysis.
    """
    placed, unplaced = {}, []
    timed = [a for a in actions if a.timestamp]
    for e in sorted(entries, key=lambda e: e["obj"].created_at):
        before = [a for a in timed if a.timestamp <= e["obj"].created_at]
        if before:
            placed.setdefault(before[-1].pk, []).append(e)
        else:
            unplaced.append(e)
    return placed, unplaced


def _redirect_back(table, number, point=""):
    url = reverse("poker:hand", args=[table, number])
    return redirect(f"{url}#pt-{point}" if point else url)


@require_POST
def request_analysis(request, table, number):
    hand = get_object_or_404(Hand, table__name=table, number=number)
    point = request.POST.get("point", "").strip()
    live = live_point(hand)
    ts = djtz.now().strftime("%Y%m%d%H%M%S%f")
    if point:
        if live and point == live.point:
            key, anchor = f"req-{hand.last_sequence}-{ts}", "live"
        elif point in {d.point for d in my_decisions(hand)}:
            key, anchor = f"pt-{point}-{ts}", point
        else:
            return HttpResponseBadRequest("not one of my decision points")
    else:
        point = live.point if live else ""
        key, anchor = f"req-{hand.last_sequence}-{ts}", "live" if live else ""
    Recommendation.objects.create(
        hand=hand, point=point, decision_key=key, requested=True, status=Recommendation.PENDING
    )
    return _redirect_back(table, number, anchor)


@require_POST
def reveal_recommendation(request, table, number, pk):
    rec = get_object_or_404(Recommendation, pk=pk, hand__table__name=table, hand__number=number)
    if rec.status == Recommendation.DONE and rec.revealed_at is None:
        rec.revealed_at = djtz.now()
        rec.save(update_fields=["revealed_at"])
    return _redirect_back(table, number, "live" if rec.point else "")


def steps_for(rec, show_moves):
    """Progress steps for display; a model's move is withheld until I may see it."""
    from .advice.engine import default_steps

    out = []
    for st in rec.steps or default_steps():
        st = dict(st)
        if not show_moves:
            st.pop("detail", None)
        out.append(st)
    return out


def queue_ahead(rec) -> int:
    """How many analyses the worker will handle before this one."""
    if rec.status != Recommendation.PENDING:
        return 0
    running = Recommendation.objects.filter(status=Recommendation.RUNNING).count()
    ids = list(engine.queue().values_list("id", flat=True))
    return running + (ids.index(rec.id) if rec.id in ids else len(ids))


def recommendation_progress(request, table, number, pk):
    rec = get_object_or_404(Recommendation, pk=pk, hand__table__name=table, hand__number=number)
    show_moves = bool(rec.actual) or rec.revealed_at is not None
    return JsonResponse({
        "status": rec.status,
        "ahead": queue_ahead(rec),
        "started": rec.started_at.isoformat() if rec.started_at else None,
        "steps": steps_for(rec, show_moves),
    })


def current(request):
    hand = current_hand()
    if hand is None:
        return render(request, "poker/empty.html")
    ctx = _detail_context(hand, request.GET.get("open", ""))
    ctx["is_current"] = True
    return render(request, "poker/hand_detail.html", ctx)


def hand_detail(request, table, number):
    hand = get_object_or_404(Hand.objects.select_related("table", "next_to_act"), table__name=table, number=number)
    ctx = _detail_context(hand, request.GET.get("open", ""))
    if not hand.is_over:
        current = current_hand()
        ctx["is_current"] = current is not None and current.pk == hand.pk
        ctx["unfinished"] = not ctx["is_current"]
    return render(request, "poker/hand_detail.html", ctx)


PROFILE_ACTION_WORDS = {"fold": "fold", "vacation_fold": "fold", "check": "check", "call": "call",
                        "bet": "bet", "raise": "raise", "all_in": "all-in"}


def player_profile(request, table, player):
    """One player's stats at a table, how the badge was derived, and their hand-by-hand record."""
    profile = get_object_or_404(
        PlayerProfile.objects.select_related("table", "player"),
        table__name=table, player__name=player,
    )
    folds, passive_acts = profile.folds, profile.checks + profile.calls
    aggressive_acts = profile.bets + profile.raises + profile.all_ins
    acted = folds + passive_acts + aggressive_acts
    fold_rate = folds / acted if acted else None
    aggr = aggressive_acts / (passive_acts + aggressive_acts) if passive_acts + aggressive_acts else None

    hands = []
    seats = {s.hand_id: s for s in profile.player.seats.filter(hand__table=profile.table)}
    by_hand = OrderedDict()
    for a in Action.objects.filter(hand__table=profile.table, player=profile.player).exclude(
        hand__number=0
    ).select_related("hand"):
        by_hand.setdefault(a.hand, []).append(a)
    for hand, acts in sorted(by_hand.items(), key=lambda kv: -kv[0].number):
        seat = seats.get(hand.id)
        delta = None
        if hand.is_over and seat and seat.bankroll_start is not None and seat.bankroll_end is not None:
            delta = seat.bankroll_end - seat.bankroll_start
        words = [PROFILE_ACTION_WORDS[a.kind] for a in acts if a.kind in PROFILE_ACTION_WORDS]
        hands.append({"hand": hand, "actions": ", ".join(words) or "–", "delta": delta})

    return render(request, "poker/player_profile.html", {
        "profile": profile,
        "blurb": PROFILE_BLURBS.get(profile.classification, ""),
        "fold_rate": fold_rate,
        "aggr": aggr,
        "min_hands": MIN_HANDS_FOR_BADGE,
        "hands": hands,
    })


HISTORY_MODELS = ("Sonnet", "Qwen", "gpt-oss", "Qwen Flash")
ROUND_LETTERS = {1: "P", 2: "F", 3: "T", 4: "R"}
HISTORY_TOLERANCE = 0.5  # model and I "agree" when our amounts are within 50%
HISTORY_GRADE_CSS = {grading.AGREE: "agree", grading.SIZE: "size", grading.DIFFERENT: "differ"}
HISTORY_GRADE_WORDS = {grading.AGREE: "agrees", grading.SIZE: "same action, different size",
                       grading.DIFFERENT: "different action"}
POINT_RE = re.compile(r"^r(\d+)n\d+$")


def _history_grades():
    """{hand_pk: [{letter, cells: [{css, title} | None per HISTORY_MODELS]}]}: one row per
    betting round, grading each model against what I actually did there. A round with
    several of my decisions shows each model's worst grade; the newest analysis of a
    decision wins."""
    latest = {}
    for rec in (
        Recommendation.objects.filter(status=Recommendation.DONE, hand__isnull=False)
        .exclude(actual={}).order_by("created_at")
    ):
        latest[(rec.hand_id, rec.point)] = rec
    worst = {}  # (hand, round) -> {label: (grade, title)}
    for (hid, point), rec in latest.items():
        m = POINT_RE.match(point)
        if not m:
            continue
        cells = worst.setdefault((hid, int(m[1])), {})
        snap = rec.snapshot or {}
        for o in rec.opinions:
            g = grading.grade(o, rec.actual, snap.get("to_call"), snap.get("my_stack"), HISTORY_TOLERANCE)
            if not g:
                continue
            title = f"{o['label']}: {_headline(o)} ({HISTORY_GRADE_WORDS[g]}; you {_headline(rec.actual)})"
            prev = cells.get(o["label"])
            if prev is None or grading.overall([g, prev[0]]) != prev[0]:  # g is worse
                cells[o["label"]] = (g, title)
    out = {}
    for (hid, rnd), cells in sorted(worst.items()):
        if not cells:
            continue
        out.setdefault(hid, []).append({
            "letter": ROUND_LETTERS.get(rnd, str(rnd)),
            "cells": [
                {"css": HISTORY_GRADE_CSS[cells[lbl][0]], "title": cells[lbl][1]} if lbl in cells else None
                for lbl in HISTORY_MODELS
            ],
        })
    return out


def hand_list(request):
    me = _me()
    current = current_hand()
    hands = (
        Hand.objects.dealt().select_related("table", "mine")
        .prefetch_related("seats__player")
        .order_by("table__name", "-number")
    )
    rec_by_hand = _history_grades()
    series = bankroll_series(me)
    after_by_hand = {p["hand_id"]: p["after"] for t in series for p in t["points"]}
    folded_ids = set(
        Action.objects.filter(kind__in=("fold", "vacation_fold"), player__name=me)
        .values_list("hand_id", flat=True)
    )
    groups = OrderedDict()
    for h in hands:
        mine = getattr(h, "mine", None)
        seat = next((s for s in h.seats.all() if s.player.name == me), None)
        net = None
        if h.is_over and seat and seat.bankroll_start is not None and seat.bankroll_end is not None:
            net = seat.bankroll_end - seat.bankroll_start
        status = (seat.status.strip("<>") if seat and seat.status else "")
        if not seat:
            result = ""
        elif status == "folded" or h.pk in folded_ids:
            result = "folded"
        elif me in h.winner:
            result = "won"
        elif status:
            result = status
        else:
            result = "played"
        groups.setdefault(h.table, []).append({
            "hand": h,
            "cards": mine.hole_cards if mine else "",
            "rec": rec_by_hand.get(h.pk, []),
            "result": result,
            "net": net,
            "bankroll": after_by_hand.get(h.pk),
            "winner": h.winner.splitlines()[0] if h.winner else "",
            "nwinners": len(h.winner.splitlines()) if h.winner else 0,
            "date": h.ended_at or h.started_at,
            "live": not h.is_over and current is not None and h.pk == current.pk,
            "unfinished": not h.is_over and (current is None or h.pk != current.pk),
        })
    # Most recently active table first (the real tournament before the practice round).
    def _latest(item):
        dates = [r["date"] for r in item[1] if r["date"]]
        return max(dates) if dates else _EPOCH

    ordered = sorted(groups.items(), key=_latest, reverse=True)
    return render(request, "poker/hand_list.html", {
        "groups": ordered, "me": me,
        "bankroll": series[-1]["current"] if series else None,
    })


def analytics(request):
    return render(request, "poker/analytics.html", {"nav": "analytics", "series": bankroll_series()})
