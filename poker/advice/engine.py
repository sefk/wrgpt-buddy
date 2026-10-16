"""Run a Recommendation end to end, and find the decisions that need one."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Case, IntegerField, Q, When
from django.utils import timezone

from poker.models import Hand, Recommendation

from . import grading, local, sonnet
from .points import actual_move, is_past, live_point, my_decisions
from .prompt import build_prompt

log = logging.getLogger("poker.advice")
STALE_AFTER = timedelta(minutes=45)  # longer than a run can take: 2 local + 2 Claude timeouts
# What run() writes. Never hand/table_name/hand_number: `reparse` may relink the row
# to a rebuilt Hand mid-run, and a full save would write back the deleted hand id.
RESULT_FIELDS = ["status", "started_at", "finished_at", "error", "actual", "actual_grade", "prompt",
                 "snapshot", "opinions", "judgement", "steps", "concurrence", "cost_usd"]
BACKFILL_PREFIX = "backfill-"  # decision_key of analyses queued in bulk; they run after everything else


def queue():
    """PENDING recommendations in the order the worker runs them: oldest first, backfill last."""
    return (
        Recommendation.objects.filter(status=Recommendation.PENDING)
        .annotate(backfill=Case(When(decision_key__startswith=BACKFILL_PREFIX, then=1), default=0,
                                output_field=IntegerField()))
        .order_by("backfill", "created_at", "id")
    )


def decision_key(hand: Hand) -> str:
    return f"seq{hand.last_sequence}"


def past_decision_key(point: str) -> str:
    return f"pt-{point}-{timezone.now():%Y%m%d%H%M%S%f}"


def default_steps() -> list[dict]:
    """The four steps of an analysis, all waiting."""
    return [
        *({"key": label, "label": f"{label} (local)", "status": "waiting"}
          for _, label in settings.ADVICE_LOCAL_MODELS),
        {"key": "sonnet", "label": "Sonnet's move", "status": "waiting"},
        {"key": "judge", "label": "Sonnet judges", "status": "waiting"},
    ]


def _init_steps(rec):
    rec.steps = default_steps()


def _step(rec, key, status, op=None, error=""):
    """Record a step's progress and save it right away so the page can show it."""
    now = timezone.now()
    for st in rec.steps:
        if st["key"] != key:
            continue
        st["status"] = status
        if status == "running":
            st["started"] = now.isoformat()
        else:
            st["finished"] = now.isoformat()
            if st.get("started"):
                started = datetime.fromisoformat(st["started"])
                st["seconds"] = round((now - started).total_seconds(), 1)
        if op is not None:
            st["detail"] = " ".join(str(x) for x in (op.get("action"), op.get("amount")) if x)
            error = error or op.get("error", "")
        if error:
            st["status"], st["error"] = "error", error[:300]
    rec.save(update_fields=["steps"])


def run(rec: Recommendation) -> Recommendation:
    """Fill in `rec`. Never raises; unexpected failures land in status=ERROR."""
    rec.status = Recommendation.RUNNING
    rec.started_at = timezone.now()
    rec.error = ""
    _init_steps(rec)
    rec.save(update_fields=RESULT_FIELDS)
    try:
        hand = rec.hand
        point = rec.point or None
        past = is_past(hand, point)
        actual = {}
        if past:
            dp = next((d for d in my_decisions(hand) if d.point == point), None)
            if dp is None:
                raise ValueError(f"{hand} has no decision point {point!r}")
            actual = actual_move(dp)
        rec.actual, rec.actual_grade = actual, ""
        rec.prompt, rec.snapshot = build_prompt(hand, point)
        rec.opinions, rec.judgement = [], {}
        rec.save(update_fields=RESULT_FIELDS)
        cost = 0.0

        # Locals first, one at a time (memory), then Sonnet and the judge back to
        # back so the prompt cache (5 min TTL) is still warm for the judge.
        locals_ = []
        local.ensure_server(log)
        for model, label in settings.ADVICE_LOCAL_MODELS:
            _step(rec, label, "running")
            locals_.append(local.ask(model, label, rec.prompt))
            _step(rec, label, "done", locals_[-1])
            log.info("%s: %s %s in %ss %s", rec, label, locals_[-1]["action"], locals_[-1]["seconds"], locals_[-1]["error"])
        _step(rec, "sonnet", "running")
        son, session, c = sonnet.recommend(rec.prompt)
        _step(rec, "sonnet", "done", son)
        cost += c
        log.info("%s: Sonnet %s in %ss %s", rec, son["action"], son["seconds"], son["error"])
        judgement = {}
        if session and not son["error"]:
            good = [o for o in locals_ if not o["error"]]
            if good or actual:
                _step(rec, "judge", "running")
                judgement, c = sonnet.judge(session, locals_, rec.snapshot.get("to_call"), actual or None)
                _step(rec, "judge", "done", error=judgement.get("error", ""))
                cost += c
                log.info("%s: judge preferred=%s cache_read=%s %s", rec, judgement.get("preferred"),
                         judgement.get("cache_read_tokens"), judgement.get("error", ""))

        for st in rec.steps:
            if st["status"] == "waiting":
                st["status"] = "skipped"
        for o in locals_:
            o["grade"] = grading.grade(o, son, rec.snapshot.get("to_call"), rec.snapshot.get("my_stack"))
        if actual:
            rec.actual_grade = grading.grade(actual, son, rec.snapshot.get("to_call"), rec.snapshot.get("my_stack"))
        rec.opinions = [son, *locals_]
        rec.judgement = judgement
        rec.concurrence = grading.overall(o["grade"] for o in locals_)
        rec.cost_usd = Decimal(str(round(cost, 4)))
        if sonnet.is_auth_error(son.get("error", "")):
            rec.status = Recommendation.ERROR
            rec.error = son["error"]
        elif all(o["error"] for o in rec.opinions):
            rec.status = Recommendation.ERROR
            rec.error = "; ".join(f"{o['label']}: {o['error']}" for o in rec.opinions)
        else:
            rec.status = Recommendation.DONE
    except Exception:  # noqa: BLE001
        rec.status = Recommendation.ERROR
        rec.error = traceback.format_exc()
        log.exception("%s failed", rec)
    rec.finished_at = timezone.now()
    try:
        rec.save(update_fields=RESULT_FIELDS)
    except Exception:  # noqa: BLE001 - run() never raises; the worker resets it as stale
        log.exception("%s: could not save the result", rec)
    return rec


def i_am_to_act(hand: Hand) -> bool:
    """My turn per the latest broadcast (next_to_act), not folded, with my cards known.

    A newer private reminder is not consulted: broadcasts carry next_to_act for
    every action and arrive at least as promptly.
    """
    me = settings.WRGPT_PLAYER_NAME
    if hand.is_over or not hand.next_to_act or hand.next_to_act.name != me:
        return False
    mine = getattr(hand, "mine", None)
    if mine is None or not mine.hole_cards:
        return False
    return not hand.seats.filter(player__name=me, status="folded").exists()


def due_decisions() -> list[Recommendation]:
    """Create PENDING recommendations for decisions that need one; return the new ones."""
    created = []
    qs = Hand.objects.dealt().filter(is_over=False, next_to_act__name=settings.WRGPT_PLAYER_NAME)
    for hand in qs.select_related("table", "next_to_act"):
        if not i_am_to_act(hand):
            continue
        n = hand.last_sequence
        if Recommendation.objects.filter(hand=hand).filter(
            Q(decision_key=f"seq{n}") | Q(decision_key__startswith=f"req-{n}-")
        ).exists():
            continue
        lp = live_point(hand)
        rec, new = Recommendation.objects.get_or_create(
            hand=hand, decision_key=decision_key(hand), defaults={"point": lp.point if lp else ""}
        )
        if new:
            created.append(rec)
    return created


def reset_stale(everything: bool = False) -> int:
    """Put RUNNING rows back to PENDING: those older than STALE_AFTER, or all of them
    (at worker startup: the worker is the only long-lived runner, so a RUNNING row then
    belongs to a process that died)."""
    qs = Recommendation.objects.filter(status=Recommendation.RUNNING)
    if not everything:
        qs = qs.filter(started_at__lt=timezone.now() - STALE_AFTER)
    return qs.update(status=Recommendation.PENDING)
