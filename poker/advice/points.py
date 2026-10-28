"""My decision points within a hand.

A point is "r<betting_round>n<k>": my k-th decision (1-based) on that street. It is
derived from the action log, so it survives `reparse` (which rebuilds Action rows),
and the live decision gets the same name as the action I eventually take there.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from ..models import Action, Hand

# Action kinds that are a decision I made (blinds, antes, vacation folds are not).
DECISION_KINDS = {"fold", "check", "call", "bet", "raise", "all_in"}


@dataclass(frozen=True)
class DecisionPoint:
    point: str
    betting_round: int
    k: int
    action: Action | None  # what I did there; None for the live decision


def point_name(betting_round: int, k: int) -> str:
    return f"r{betting_round}n{k}"


def my_decisions(hand: Hand, me: str | None = None) -> list[DecisionPoint]:
    """Each decision I made in this hand, in order."""
    me = me or settings.WRGPT_PLAYER_NAME
    counts: dict[int, int] = {}
    out = []
    for a in hand.actions.filter(player__name=me, kind__in=DECISION_KINDS).order_by("order"):
        rnd = a.betting_round or 1
        counts[rnd] = counts.get(rnd, 0) + 1
        out.append(DecisionPoint(point_name(rnd, counts[rnd]), rnd, counts[rnd], a))
    return out


def live_point(hand: Hand, me: str | None = None) -> DecisionPoint | None:
    """The decision I'm facing now, or None if it isn't my turn."""
    me = me or settings.WRGPT_PLAYER_NAME
    if hand.is_over or not hand.next_to_act or hand.next_to_act.name != me:
        return None
    rnd = hand.betting_round or 1
    k = sum(1 for d in my_decisions(hand, me) if d.betting_round == rnd) + 1
    return DecisionPoint(point_name(rnd, k), rnd, k, None)


def is_past(hand: Hand, point: str | None, me: str | None = None) -> bool:
    """True when `point` names a decision that is not the live one (so it needs a replay)."""
    if not point:
        return False
    live = live_point(hand, me)
    return live is None or live.point != point


def actual_move(dp: DecisionPoint) -> dict:
    """What I actually did at a decision: {action, amount, text}.

    amount is the TOTAL I had in this street for bet/raise (the prompt's convention), else None.
    """
    a = dp.action
    if a is None:
        return {}
    amount = None
    if a.kind == "bet":
        amount = a.amount
    elif a.kind == "raise":
        amount = a.total
    return {"action": a.kind, "amount": amount, "text": a.text}


def settle_actuals(hand: Hand, me: str | None = None) -> int:
    """Record my real move on finished analyses made while the decision was still live.

    Those carry actual={} (run() only knows the move for past points); once I have acted the
    move is in the action log, so fill in `actual` and its grade against Sonnet. The judge's
    `on_actual` comment needs another Sonnet turn and is left to a re-run. Returns the count.
    """
    from . import grading
    from ..models import Recommendation

    pending = list(
        hand.recommendations.filter(status=Recommendation.DONE, actual={}).exclude(point="")
    )
    if not pending:
        return 0
    moves = {d.point: actual_move(d) for d in my_decisions(hand, me)}
    n = 0
    for rec in pending:
        actual = moves.get(rec.point)
        if not actual:
            continue
        rec.actual = actual
        son = next((o for o in rec.opinions or [] if o.get("label") == "Sonnet"), None)
        if son:
            snap = rec.snapshot or {}
            rec.actual_grade = grading.grade(actual, son, snap.get("to_call"), snap.get("my_stack"))
        rec.save(update_fields=["actual", "actual_grade"])
        n += 1
    return n
