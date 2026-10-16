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
