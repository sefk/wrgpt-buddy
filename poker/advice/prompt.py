"""Build the plain-text situation description sent to every model."""

from __future__ import annotations

from django.conf import settings

ROUND_NAMES = {1: "preflop", 2: "flop", 3: "turn", 4: "river"}
ACTIONS = ["fold", "check", "call", "bet", "raise", "all_in"]

# JSON schema for a single recommendation (used by the local models directly and
# nested inside the combined Sonnet schema).
RECOMMENDATION_PROPERTIES = {
    "action": {"type": "string", "enum": ACTIONS},
    "amount": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    "command": {"type": "string"},
    "reasoning": {"type": "string"},
}
RECOMMENDATION_SCHEMA = {
    "type": "object",
    "properties": RECOMMENDATION_PROPERTIES,
    "required": list(RECOMMENDATION_PROPERTIES),
    "additionalProperties": False,
}

INSTRUCTIONS = """\
You are advising a no-limit hold'em player in a slow email tournament. Recommend the single best action for the player ("you") right now.

Answer with JSON containing:
- action: one of fold, check, call, bet, raise, all_in.
- amount: integer dollars, or null for fold/check/call. For bet and raise this is the TOTAL amount you will have put in during THIS betting round (including chips already committed this round, e.g. a blind), not just the increment. For all_in use your whole remaining stack.
- command: the exact dealer command text to email, e.g. "$0" (check or fold, whichever applies), "call *" (call any amount), "BET $1200" (the $ amount is what you add this round on top of chips already committed; so with $100 already in, a total of $1200 is "BET $1100"), "POT", "JAM" (all in).
- reasoning: 3-6 concise sentences covering hand strength, position, pot odds, and what the opponents' actions suggest."""


def _by_street(actions):
    streets: dict[int | None, list] = {}
    for a in actions:
        streets.setdefault(a.betting_round, []).append(a)
    return streets


def _committed(actions, me_name, rnd):
    """Chips I have already put in during betting round `rnd` (best effort)."""
    total = 0
    for a in actions:
        if a.betting_round != rnd or not a.player or a.player.name != me_name:
            continue
        if a.kind in ("blind", "call", "bet") and a.amount:
            total += a.amount
        elif a.kind == "raise":
            total = a.total or (total + (a.amount or 0))
    return total


def build_prompt(hand) -> tuple[str, dict]:
    me_name = settings.WRGPT_PLAYER_NAME
    mine = getattr(hand, "mine", None)
    hole = (mine.hole_cards if mine else "") or ""
    seats = list(hand.seats.select_related("player").order_by("seat_number"))
    actions = list(hand.actions.select_related("player").order_by("order"))
    me_seat = next((s for s in seats if s.player.name == me_name), None)
    my_stack = me_seat.bankroll_start if me_seat else None

    i_am_next = bool(hand.next_to_act and hand.next_to_act.name == me_name)
    to_call = hand.to_call if (i_am_next and hand.to_call is not None) else None
    if to_call is None and mine and mine.to_call is not None:
        to_call = mine.to_call
    pot = hand.pot if hand.pot is not None else (mine.pot if mine else None)
    rnd = hand.betting_round
    rname = ROUND_NAMES.get(rnd, f"round {rnd}") if rnd else "unknown"

    snapshot = {
        "cards": hole,
        "board": hand.board or "",
        "pot": pot,
        "to_call": to_call,
        "betting_round": rname,
        "next_to_act": hand.next_to_act.name if hand.next_to_act else None,
        "my_stack": my_stack,
    }

    L = []
    L.append(
        "Tournament: WRGPT, a slow email no-limit hold'em tournament; decisions take "
        "hours, so think carefully rather than quickly. Stacks are in dollars."
    )
    L.append(f"Table {hand.table.name}, hand {hand.number}" + (f", day {hand.day}" if hand.day else "") + ".")
    L.append(f"Betting round: {rname}.")
    if hand.next_timeout:
        L.append(f"Action timeout: {hand.next_timeout:%Y-%m-%d %H:%M} UTC.")
    L.append("")

    dealer_idx = next((i for i, s in enumerate(seats) if s.is_dealer), None)
    L.append(f"You are {me_name}. Seats in order (stack at the start of the hand):")
    for i, s in enumerate(seats):
        tags = []
        if s.is_dealer:
            tags.append("dealer button")
        if s.player.name == me_name:
            tags.append("YOU")
        if hand.next_to_act and s.player_id == hand.next_to_act_id:
            tags.append("next to act")
        st = s.status or "in"
        if st == "folded":
            st = "FOLDED"
        stack = f"${s.bankroll_start:,}" if s.bankroll_start is not None else "?"
        L.append(f"  seat {s.seat_number}: {s.player.name}  {stack}  [{st}]" + (f"  ({', '.join(tags)})" if tags else ""))
    live = [s for s in seats if s.status not in ("folded",)]
    L.append(f"Players still in the hand: {len(live)}.")
    if dealer_idx is not None and me_seat is not None:
        my_idx = seats.index(me_seat)
        n = len(seats)
        off = (my_idx - dealer_idx) % n
        if off == 0:
            pos = "on the button (dealer)"
        else:
            pos = f"{off} seat(s) after the button (button = 0; small blind = 1, big blind = 2, the last seat before the button is the cutoff)"
        L.append(f"Your position: {pos}; {n} seats at the table.")
    L.append("")

    L.append(f"Your hole cards: {hole or 'unknown'}")
    L.append(f"Board: {hand.board or '(none yet)'}")
    L.append(f"Pot: {('$' + format(pot, ',')) if pot is not None else '?'}")
    L.append(f"Amount to call: {('$' + format(to_call, ',')) if to_call is not None else '?'}")
    if my_stack is not None:
        L.append(f"Your stack: ${my_stack:,}")
    if rnd:
        L.append(f"Chips you have already committed this betting round: ${_committed(actions, me_name, rnd):,}")
    if mine:
        if mine.advance_action:
            L.append(f"Pending advance action on file with the dealer: {mine.advance_action}")
        if mine.last_command:
            L.append(f"Your last command to the dealer: {mine.last_command}")
        if mine.has_unresolved_error:
            L.append(f"Dealer rejected your last command: {mine.last_error}")
    L.append("")

    L.append("Action so far:")
    if not actions:
        L.append("  (no actions recorded)")
    for rnd_key, rows in _by_street(actions).items():
        label = ROUND_NAMES.get(rnd_key, "before the deal") if rnd_key else "before the deal"
        L.append(f"{label.capitalize()}:")
        for a in rows:
            if a.kind == "table_talk":
                who = a.player.name if a.player else "?"
                L.append(f"  [talk] {who}: {a.text}")
            elif a.kind == "players_left":
                continue
            else:
                L.append(f"  {a.text}")
    L.append("")
    L.append(INSTRUCTIONS)
    return "\n".join(L), snapshot
