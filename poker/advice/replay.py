"""Reconstruct the table as it stood just before one of my past decisions.

Everything is derived from the Action log (plus Seat.bankroll_start and MyHand), and
nothing after the decision leaks in: `ReplayState.log` stops just before my action.

Chip accounting, per the dealer's log conventions:
- "X blinds $N" / "X antes $N": N chips in. A blind counts toward the street's bet
  (preflop), an ante is dead money.
- "X bets $N": N added; "X raises $A to $T total": T is X's total this street;
  "X calls": no amount, X adds (current bet - X's chips this street), capped by stack.
- "Pot right ($N)" opens the next street and resets the per-street bet.
- Seat.bankroll_start comes from the first broadcast of the hand, which is printed
  after the blinds were posted, so a player's stack at the deal is
  bankroll_start + their blind/ante (verified against bankroll_end; see `validate`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings

from ..models import Action, Hand
from .points import DecisionPoint, my_decisions

OUT_KINDS = {"fold", "vacation_fold"}
ROUND_NAMES = {1: "preflop", 2: "flop", 3: "turn", 4: "river"}


@dataclass
class ReplayState:
    hand: Hand
    point: DecisionPoint
    betting_round: int
    street: str
    board: list[str]
    log: list[Action]  # every action strictly before my decision
    pot: int
    current_bet: int  # total this street a player must have in to stay in
    my_committed: int  # my chips in this street
    to_call: int
    stacks: dict[str, int]  # chips behind, per player, at that moment
    in_hand: dict[str, bool]
    all_in: set[str]
    seat_numbers: dict[str, int]
    dealer: str | None
    hole_cards: str
    me: str
    # (betting_round, dealer's stated pot, our computed pot) at each street boundary passed
    boundaries: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def my_stack(self) -> int | None:
        return self.stacks.get(self.me)


def cards_in(action: Action) -> list[str]:
    """Cards named in a BOARD_CARDS line, e.g. 'Flopped cards: As 6c Th'."""
    return [c for c in action.text.split(":", 1)[-1].split() if c != "?"]


def _walk(hand: Hand, actions: list[Action], me: str, stop_at: Action | None):
    """Core accounting. Returns the running state after the actions before `stop_at`."""
    seats = list(hand.seats.select_related("player"))
    start = {s.player.name: (s.bankroll_start or 0) for s in seats}
    # Seat.bankroll_start is post-blind: add blinds/antes back to get the deal stack.
    for a in actions:
        if a.kind in ("blind", "ante") and a.player and a.amount:
            start[a.player.name] = start.get(a.player.name, 0) + a.amount
    total = {n: 0 for n in start}
    street = {n: 0 for n in start}
    in_hand = {n: True for n in start}
    all_in: set[str] = set()
    board: list[str] = []
    cur_bet = 0
    rnd = 1
    boundaries = []
    log = []

    def pot():
        return sum(total.values())

    def put(name, delta):
        delta = max(0, min(delta, start.get(name, 0) - total.get(name, 0)))
        total[name] = total.get(name, 0) + delta
        street[name] = street.get(name, 0) + delta
        if start.get(name, 0) - total[name] <= 0:
            all_in.add(name)
        return delta

    for a in actions:
        if stop_at is not None and a.id == stop_at.id:
            break
        log.append(a)
        n = a.player.name if a.player else None
        if a.betting_round:
            rnd = a.betting_round
        k = a.kind
        if k == "pot_right":
            boundaries.append((a.betting_round or rnd, a.amount, pot()))
            street = {x: 0 for x in street}
            cur_bet = 0
        elif k == "board_cards":
            board += cards_in(a)
        elif n is None:
            continue
        elif k == "ante":
            if a.amount:
                put(n, a.amount)
                street[n] -= a.amount  # dead money, not part of the street's bet
        elif k == "blind":
            put(n, a.amount or 0)
            cur_bet = max(cur_bet, street[n])
        elif k == "bet":
            put(n, a.amount or 0)
            cur_bet = max(cur_bet, street[n])
        elif k == "raise":
            target = a.total if a.total is not None else street.get(n, 0) + (a.amount or 0)
            put(n, target - street.get(n, 0))
            cur_bet = max(cur_bet, street[n])
        elif k == "call":
            put(n, a.amount if a.amount else cur_bet - street.get(n, 0))
        elif k == "all_in":
            put(n, start.get(n, 0) - total.get(n, 0))
            cur_bet = max(cur_bet, street[n])
        elif k in OUT_KINDS:
            in_hand[n] = False
    return dict(start=start, total=total, street=street, in_hand=in_hand, all_in=all_in,
                board=board, cur_bet=cur_bet, rnd=rnd, boundaries=boundaries, log=log,
                pot=pot(), seats=seats)


def replay(hand: Hand, point: str, me: str | None = None) -> ReplayState:
    """The situation immediately before my decision `point` ('r2n1', ...)."""
    me = me or settings.WRGPT_PLAYER_NAME
    dp = next((d for d in my_decisions(hand, me) if d.point == point), None)
    if dp is None:
        raise ValueError(f"{hand} has no decision point {point!r} for {me}")
    actions = list(hand.actions.select_related("player").order_by("order"))
    w = _walk(hand, actions, me, dp.action)
    mine = getattr(hand, "mine", None)
    stacks = {n: w["start"][n] - w["total"][n] for n in w["start"]}
    my_comm = w["street"].get(me, 0)
    return ReplayState(
        hand=hand, point=dp, betting_round=dp.betting_round,
        street=ROUND_NAMES.get(dp.betting_round, f"round {dp.betting_round}"),
        board=w["board"], log=w["log"], pot=w["pot"], current_bet=w["cur_bet"],
        my_committed=my_comm, to_call=max(0, min(w["cur_bet"] - my_comm, stacks.get(me, 0))),
        stacks=stacks, in_hand=w["in_hand"], all_in=w["all_in"],
        seat_numbers={s.player.name: s.seat_number for s in w["seats"]},
        dealer=next((s.player.name for s in w["seats"] if s.is_dealer), None),
        hole_cards=(mine.hole_cards if mine else "") or "", me=me,
        boundaries=w["boundaries"],
    )


def validate(hand: Hand) -> list[tuple[int, int, int]]:
    """Replay the whole hand and return (round, dealer pot, our pot) per street boundary."""
    actions = list(hand.actions.select_related("player").order_by("order"))
    return _walk(hand, actions, "", None)["boundaries"]


def validate_stacks(hand: Hand) -> list[tuple[str, int, int]]:
    """For finished hands with full history: (player, start by replay, start from bankroll_end).

    start-from-end = bankroll_end + chips put in - chips won. They should be equal.
    """
    actions = list(hand.actions.select_related("player").order_by("order"))
    w = _walk(hand, actions, "", None)
    won: dict[str, int] = {}
    for a in actions:
        if a.kind == "wins" and a.player:
            won[a.player.name] = won.get(a.player.name, 0) + (a.amount or 0)
    out = []
    for s in w["seats"]:
        n = s.player.name
        if s.bankroll_end is None:
            continue
        out.append((n, w["start"][n], s.bankroll_end + w["total"][n] - won.get(n, 0)))
    return out
