"""Build the plain-text situation description sent to every model."""

from __future__ import annotations

from django.conf import settings

from .points import is_past
from .replay import replay

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


def build_prompt(hand, point: str | None = None) -> tuple[str, dict]:
    """Prompt + snapshot for the live decision (point None or the live point) or a past one."""
    if is_past(hand, point):
        return build_past_prompt(hand, point)
    return build_live_prompt(hand)


def _position_line(order, dealer_name, me_name):
    """`order`: player names in seat order."""
    if dealer_name not in order or me_name not in order:
        return None
    off = (order.index(me_name) - order.index(dealer_name)) % len(order)
    if off == 0:
        pos = "on the button (dealer)"
    else:
        pos = f"{off} seat(s) after the button (button = 0; small blind = 1, big blind = 2, the last seat before the button is the cutoff)"
    return f"Your position: {pos}; {len(order)} seats at the table."


def build_past_prompt(hand, point: str) -> tuple[str, dict]:
    """The situation immediately before my decision `point`, with no hindsight.

    Built only from the replay (actions before the decision), my hole cards and the
    seat list; end-of-hand state (Seat.status, final bankrolls, results, later board
    cards, later talk) is deliberately not consulted.
    """
    rs = replay(hand, point)
    me = rs.me
    money = lambda v: "?" if v is None else f"${v:,}"  # noqa: E731
    names = sorted(rs.seat_numbers, key=rs.seat_numbers.get)
    snapshot = {
        "cards": rs.hole_cards,
        "board": " ".join(rs.board),
        "pot": rs.pot,
        "to_call": rs.to_call,
        "betting_round": rs.street,
        "next_to_act": me,
        "my_stack": rs.my_stack,
    }
    L = [
        "Tournament: WRGPT, a slow email no-limit hold'em tournament; decisions take "
        "hours, so think carefully rather than quickly. Stacks are in dollars.",
        f"Table {hand.table.name}, hand {hand.number}" + (f", day {hand.day}" if hand.day else "") + ".",
        f"Betting round: {rs.street}.",
        "",
        f"You are {me}. Seats in order (stacks as they stand right now):",
    ]
    for n in names:
        tags = []
        if n == rs.dealer:
            tags.append("dealer button")
        if n == me:
            tags.append("YOU")
            tags.append("next to act")
        st = "in" if rs.in_hand.get(n) else "FOLDED"
        if n in rs.all_in and rs.in_hand.get(n):
            st = "ALL IN"
        L.append(f"  seat {rs.seat_numbers[n]}: {n}  {money(rs.stacks.get(n))}  [{st}]"
                 + (f"  ({', '.join(tags)})" if tags else ""))
    L.append(f"Players still in the hand: {sum(1 for v in rs.in_hand.values() if v)}.")
    pos = _position_line(names, rs.dealer, me)
    if pos:
        L.append(pos)
    L.append("")
    L.append(f"Your hole cards: {rs.hole_cards or 'unknown'}")
    L.append(f"Board: {' '.join(rs.board) or '(none yet)'}")
    L.append(f"Pot: {money(rs.pot)}")
    L.append(f"Amount to call: {money(rs.to_call)}")
    if rs.my_stack is not None:
        L.append(f"Your stack: {money(rs.my_stack)}")
    L.append(f"Chips you have already committed this betting round: {money(rs.my_committed)}")
    L.append("")
    L.append("Action so far:")
    shown = [a for a in rs.log if a.kind != "players_left"]
    if not shown:
        L.append("  (no actions recorded)")
    last = object()
    for a in shown:
        if a.betting_round != last:
            last = a.betting_round
            label = ROUND_NAMES.get(a.betting_round, "before the deal") if a.betting_round else "before the deal"
            L.append(f"{label.capitalize()}:")
        if a.kind == "table_talk":
            L.append(f"  [talk] {a.player.name if a.player else '?'}: {a.text}")
        else:
            L.append(f"  {a.text}")
    L.append("")
    L.append(INSTRUCTIONS)
    return "\n".join(L), snapshot


def build_live_prompt(hand) -> tuple[str, dict]:
    me_name = settings.WRGPT_PLAYER_NAME
    mine = getattr(hand, "mine", None)
    hole = (mine.hole_cards if mine else "") or ""
    seats = list(hand.seats.select_related("player").order_by("seat_number"))
    actions = list(hand.actions.select_related("player").order_by("order"))
    me_seat = next((s for s in seats if s.player.name == me_name), None)
    # Chips behind right now: start-of-hand stacks minus everything put in so far.
    from .replay import _walk

    w = _walk(hand, actions, me_name, None)
    stacks_now = {n: w["start"][n] - w["total"][n] for n in w["start"]}
    my_stack = stacks_now.get(me_name) if me_seat else None

    i_am_next = bool(hand.next_to_act and hand.next_to_act.name == me_name)
    if i_am_next:
        # "sefk is first to act" carries no amount: an unopened street, $0 to call. An
        # older private reminder's amount would be stale.
        to_call = hand.to_call or 0
    else:
        to_call = mine.to_call if mine else None
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
    L.append(f"You are {me_name}. Seats in order (chips behind right now):")
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
        behind = stacks_now.get(s.player.name)
        stack = f"${behind:,}" if behind is not None else "?"
        L.append(f"  seat {s.seat_number}: {s.player.name}  {stack}  [{st}]" + (f"  ({', '.join(tags)})" if tags else ""))
    live = [s for s in seats if s.status not in ("folded",)]
    L.append(f"Players still in the hand: {len(live)}.")
    if dealer_idx is not None and me_seat is not None:
        L.append(_position_line([s.player.name for s in seats], seats[dealer_idx].player.name, me_name))
    L.append("")

    L.append(f"Your hole cards: {hole or 'unknown'}")
    L.append(f"Board: {hand.board or '(none yet)'}")
    L.append(f"Pot: {('$' + format(pot, ',')) if pot is not None else '?'}")
    L.append(f"Amount to call: {('$' + format(to_call, ',')) if to_call is not None else '?'}")
    if my_stack is not None:
        L.append(f"Your stack: ${my_stack:,}")
    if rnd:
        committed = w["street"].get(me_name, 0) if w["rnd"] == rnd else 0  # nothing yet on a new street
        L.append(f"Chips you have already committed this betting round: ${committed:,}")
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
