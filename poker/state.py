"""Apply parsed emails to the derived tables. RawEmail is the source of truth."""

from __future__ import annotations

import hashlib

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .advice.points import settle_actuals
from .models import Action, Hand, MyHand, Player, PlayerProfile, RawEmail, Seat, Table
from .parsing.types import (
    AdvanceAck,
    DealerError,
    Broadcast,
    EventKind,
    HandEvent,
    MyReply,
    ParsedEmail,
    PrivateState,
)


def _player(name):
    if not name:
        return None
    return Player.objects.get_or_create(name=name)[0]


def _board_str(board):
    return " ".join(c for c in (board or ()) if c)


def _get_hand(table_name, number, status_url=None):
    table, _ = Table.objects.get_or_create(name=table_name)
    if status_url and table.status_url != status_url:
        table.status_url = status_url
        table.save(update_fields=["status_url"])
    hand, _ = Hand.objects.get_or_create(table=table, number=number)
    return hand


def _make_actions(hand, raw, events, start_round, order_base):
    """Build Action rows; betting_round bumps at each POT_RIGHT."""
    rnd = start_round
    rows = []
    for i, ev in enumerate(events):
        if ev.kind == EventKind.POT_RIGHT and rnd is not None:
            rnd += 1
        rows.append(
            Action(
                hand=hand,
                player=_player(ev.player),
                kind=str(ev.kind),
                amount=ev.amount,
                total=ev.total,
                timestamp=ev.timestamp,
                betting_round=rnd,
                order=order_base + i,
                text=ev.text,
                source=raw,
            )
        )
    return rows


def _apply_broadcast(raw: RawEmail, b: Broadcast) -> None:
    hand = _get_hand(b.table, b.hand_number, b.status_url)
    if b.sequence not in hand.sequences:
        hand.sequences = sorted([*hand.sequences, b.sequence])

    # --- action log ---
    if b.history:
        Action.objects.filter(hand=hand).delete()
        Action.objects.bulk_create(_make_actions(hand, raw, b.history, 1, 0))
        hand.has_history = True
        stamps = [e.timestamp for e in b.history if e.timestamp]
        if stamps:
            hand.started_at = min(stamps)
            hand.ended_at = max(stamps)
        wins = [e.text for e in b.history if e.kind == EventKind.WINS]
        if wins:
            hand.winner = "\n".join(wins)
    elif not hand.has_history:
        Action.objects.filter(hand=hand, source=raw).delete()
        start = None
        if b.betting_round is not None:
            start = b.betting_round - sum(e.kind == EventKind.POT_RIGHT for e in b.events)
        Action.objects.bulk_create(
            _make_actions(hand, raw, b.events, start, b.sequence * 1000)
        )
        wins = [e.text for e in b.events if e.kind == EventKind.WINS]
        if wins and not hand.winner:
            hand.winner = "\n".join(wins)

    # --- current state: only advance on a newer sequence ---
    if b.sequence > hand.last_sequence and hand.is_over and not b.hand_over:
        # A finished hand can still get stray broadcasts (e.g. a player being
        # moved when the table breaks). Don't let them reopen it.
        hand.last_sequence = b.sequence
    elif b.sequence > hand.last_sequence:
        hand.last_sequence = b.sequence
        hand.day = b.day
        hand.betting_round = b.betting_round
        if b.board is not None:
            hand.board = _board_str(b.board)
        hand.pot = b.pot
        hand.to_call = b.to_call
        hand.next_to_act = _player(b.next_to_act)
        hand.next_timeout = b.next_timeout
        hand.is_over = b.hand_over
        if b.hand_over:
            hand.next_to_act = None
            hand.next_timeout = None
            hand.to_call = None
        _apply_seats(hand, b)
    elif b.hand_over and not hand.is_over:
        hand.is_over = True
    if hand.sequences and b.sequence == hand.sequences[0] and b.seats:
        _apply_start_bankrolls(hand, b)
    hand.save()
    settle_actuals(hand)
    _clear_consumed_advance(raw, hand, b)


_ACTION_KINDS = {EventKind.FOLD, EventKind.CHECK, EventKind.CALL, EventKind.BET, EventKind.RAISE, EventKind.ALL_IN}


def _clear_consumed_advance(raw: RawEmail, hand: Hand, b: Broadcast) -> None:
    """Drop my advance action once the dealer has used it.

    Per the manual an advance action runs the next time it is my turn, then is
    erased, unless it is sticky, in which case it lasts until the hand ends.
    So: a broadcast sent after the acknowledgement that shows an action by me
    (settings.WRGPT_PLAYER_NAME) clears a non-sticky advance action, and a
    broadcast that ends the hand clears any advance action. Broadcasts sent
    before the acknowledgement (out-of-order arrival, or the action that
    predates it) never clear it. History lines are ignored for the action
    check (they replay the whole hand) but a final broadcast still ends it.
    """
    mine = MyHand.objects.filter(hand=hand).first()
    if mine is None or not mine.advance_action or raw.sent_at is None:
        return
    if mine.advance_action_at is not None and raw.sent_at <= mine.advance_action_at:
        return
    me = settings.WRGPT_PLAYER_NAME
    acted = any(e.kind in _ACTION_KINDS and e.player == me for e in b.events)
    if b.hand_over or (acted and not mine.advance_sticky):
        mine.advance_action = ""
        mine.advance_sticky = False
        mine.save(update_fields=["advance_action", "advance_sticky"])


def _apply_seats(hand, b):
    for s in b.seats:
        p = _player(s.name)
        seat, created = Seat.objects.get_or_create(
            hand=hand, player=p, defaults={"seat_number": s.seat, "bankroll_start": s.bankroll}
        )
        seat.seat_number = s.seat
        seat.is_dealer = s.is_dealer
        seat.status = s.status
        seat.bankroll_now = s.bankroll
        seat.save()
    # The final broadcast's second table is the NEXT hand's seating (the button has
    # moved): take only the ending bankrolls from it.
    if b.hand_over and b.final_seats:
        for s in b.final_seats:
            Seat.objects.filter(hand=hand, player__name=s.name).update(bankroll_end=s.bankroll)


def _apply_start_bankrolls(hand, b):
    """The earliest broadcast of a hand defines its starting bankrolls (after blinds),
    even when it arrives after later ones."""
    for s in b.seats:
        Seat.objects.update_or_create(
            hand=hand, player=_player(s.name),
            defaults={"bankroll_start": s.bankroll},
            create_defaults={"seat_number": s.seat, "bankroll_start": s.bankroll,
                             "is_dealer": s.is_dealer, "status": s.status},
        )


def _apply_private(raw: RawEmail, p: PrivateState) -> MyHand:
    hand = _get_hand(p.table, p.hand_number, p.status_url)
    changed = False
    if hand.day is None and p.day is not None:
        hand.day = p.day
        changed = True
    if changed:
        hand.save(update_fields=["day"])
    mine, _ = MyHand.objects.get_or_create(hand=hand)
    if mine.private_at is None or raw.sent_at is None or raw.sent_at >= mine.private_at:
        mine.hole_cards = " ".join(p.hole_cards)
        mine.game_id = p.game_id or mine.game_id
        mine.to_call = p.to_call
        mine.pot = p.pot
        mine.start_time = p.start_time
        mine.next_timeout = p.next_timeout
        mine.private_at = raw.sent_at
        mine.save()
    return mine


def _apply_reply(raw: RawEmail, r: MyReply) -> None:
    mine = None
    if r.quoted is not None:
        mine = _apply_private(raw, r.quoted)
    elif r.game_id:
        # Game IDs are reused across hands: match the subject's hand number too.
        qs = MyHand.objects.filter(game_id=r.game_id)
        if r.hand_number is not None:
            qs = qs.filter(hand__number=r.hand_number)
        mine = qs.order_by("-hand__number", "-id").first()
    if mine is None:
        return
    if mine.last_command_at is None or raw.sent_at is None or raw.sent_at >= mine.last_command_at:
        mine.last_command = r.command
        mine.last_command_at = raw.sent_at
        mine.save(update_fields=["last_command", "last_command_at"])


def _my_hand_by_id(table, number, game_id, status_url=None) -> MyHand:
    hand = _get_hand(table, number, status_url)
    mine, _ = MyHand.objects.get_or_create(hand=hand)
    if game_id and not mine.game_id:
        mine.game_id = game_id
        mine.save(update_fields=["game_id"])
    return mine


def _apply_advance_ack(raw: RawEmail, a: AdvanceAck) -> None:
    mine = _my_hand_by_id(a.table, a.hand_number, a.game_id)
    if mine.advance_action_at is None or raw.sent_at is None or raw.sent_at >= mine.advance_action_at:
        text = a.description
        if a.table_talk:
            text += f"\nTable talk: {a.table_talk}"
        mine.advance_action = text
        mine.advance_sticky = a.sticky
        mine.advance_action_at = raw.sent_at
        mine.save(update_fields=["advance_action", "advance_sticky", "advance_action_at"])


def _apply_dealer_error(raw: RawEmail, e: DealerError) -> None:
    mine = _my_hand_by_id(e.table, e.hand_number, e.game_id)
    if mine.last_error_at is None or raw.sent_at is None or raw.sent_at >= mine.last_error_at:
        mine.last_error = e.message
        mine.last_error_at = raw.sent_at
        mine.save(update_fields=["last_error", "last_error_at"])


@transaction.atomic
def apply(raw_email: RawEmail, parsed: ParsedEmail) -> None:
    body = parsed.body
    if isinstance(body, Broadcast):
        _apply_broadcast(raw_email, body)
    elif isinstance(body, PrivateState):
        _apply_private(raw_email, body)
    elif isinstance(body, MyReply):
        _apply_reply(raw_email, body)
    elif isinstance(body, AdvanceAck):
        _apply_advance_ack(raw_email, body)
    elif isinstance(body, DealerError):
        _apply_dealer_error(raw_email, body)


def parse_raw_bytes(data: bytes) -> ParsedEmail:
    from django.conf import settings

    from .parsing import parse_email

    return parse_email(data, my_addresses=(settings.WRGPT_PLAYER_EMAIL,))


def refresh_meta(raw_email: RawEmail, parsed: ParsedEmail) -> None:
    raw_email.kind = str(parsed.meta.kind)
    raw_email.subject = parsed.meta.subject[:500]
    raw_email.sent_at = parsed.meta.date
    raw_email.parse_errors = "\n".join(parsed.errors)
    raw_email.parsed_at = timezone.now()
    raw_email.save()


def ingest(
    data: bytes, source: str, gmail_id: str | None = None, fallback_id: str = ""
) -> tuple[RawEmail, bool, ParsedEmail]:
    """Store one raw email (deduped by Message-ID), parse it, and apply it.

    Returns (raw_email, created, parsed).
    """
    parsed = parse_raw_bytes(data)
    mid = (parsed.meta.message_id or fallback_id
           or (f"gmail:{gmail_id}" if gmail_id else f"sha256:{hashlib.sha256(data).hexdigest()}"))
    # One transaction: if applying fails (e.g. "database is locked"), the RawEmail row
    # rolls back too, so the poller doesn't count the message as known and retries it.
    with transaction.atomic():
        raw, created = RawEmail.objects.get_or_create(
            message_id=mid, defaults={"raw": data, "source": source, "gmail_id": gmail_id}
        )
        if gmail_id and raw.gmail_id != gmail_id:
            raw.gmail_id = gmail_id  # first seen as an .eml file, now matched in Gmail
        refresh_meta(raw, parsed)
        apply(raw, parsed)
    return raw, created, parsed


def parse_raw(raw_email: RawEmail) -> ParsedEmail:
    """Parse a stored email and refresh its metadata columns."""
    parsed = parse_raw_bytes(bytes(raw_email.raw))
    refresh_meta(raw_email, parsed)
    return parsed


@transaction.atomic
def rebuild_all(parse=parse_raw) -> int:
    """Wipe derived tables, then re-parse and re-apply every RawEmail in sent_at order."""
    for model in (Action, MyHand, Seat, Hand, Table, Player, PlayerProfile):
        model.objects.all().delete()
    n = 0
    for raw in RawEmail.objects.order_by("sent_at", "id"):
        apply(raw, parse(raw))
        n += 1
    relink_recommendations()
    return n


def relink_recommendations() -> int:
    """Reattach recommendations (whose hand FK was nulled by a rebuild) by table + hand number."""
    from .models import Recommendation

    hands = {(h.table.name, h.number): h for h in Hand.objects.select_related("table")}
    n = 0
    for rec in Recommendation.objects.filter(hand=None).exclude(table_name=""):
        hand = hands.get((rec.table_name, rec.hand_number))
        if hand:
            rec.hand = hand
            rec.save(update_fields=["hand"])
            n += 1
    return n


# --- Player profiles: aggregation + classification ---

_ACTION_KINDS = {"fold", "check", "call", "bet", "raise", "all_in"}


MIN_HANDS_FOR_BADGE = 3

# Badge definitions, in the order the legend shows them. `_classify` decides which applies.
PROFILE_TYPES = [
    ("rock", "Folds at least 70% of the time and rarely bets or raises (aggression under 30%). Plays only strong hands, passively."),
    ("tight", "Folds at least 70% of the time, with middling aggression (30-50%)."),
    ("agro", "Folds at least 70% of the time, but bets or raises at least half the time it does play."),
    ("loose", "Folds less than 40% of the time, with middling aggression (30-50%). Plays many hands."),
    ("caller", "Folds less than 40% of the time and rarely bets or raises (aggression under 30%). Calls a lot."),
    ("maniac", "Folds less than 40% of the time and bets or raises at least half the time. Plays many hands, hard."),
    ("balanced", "Folds between 40% and 70% of the time, with no extreme in how often they bet or raise. Nothing stands out yet."),
]
PROFILE_BLURBS = dict(PROFILE_TYPES)


def _classify(hands_played: int, fold_rate: float, aggr: float) -> str:
    """Classify a player from aggregate stats.

    fold_rate = folds / (folds + checks + calls + bets + raises + all_ins)
    aggr = (bets + raises + all_ins) / (checks + calls + bets + raises + all_ins)
    """
    if hands_played < MIN_HANDS_FOR_BADGE:
        return ""

    tight = fold_rate >= 0.70
    loose = fold_rate < 0.40
    passive = aggr < 0.30
    aggressive = aggr >= 0.50

    if tight and passive:
        return "rock"
    if loose and aggressive:
        return "maniac"
    if loose and passive:
        return "caller"
    if tight and aggressive:
        return "agro"
    if loose:
        return "loose"
    if tight:
        return "tight"
    return "balanced"


def build_profiles(table_name: str) -> int:
    """Rebuild all PlayerProfile rows for one table from its actions.

    Returns the number of profiles created or updated.
    """
    from django.db.models import Case, F, Sum, When

    table = Table.objects.get(name=table_name)
    hands = Hand.objects.filter(table=table).exclude(number=0)
    actions = Action.objects.filter(hand__in=hands)

    # Per-player aggregates
    stats = {}
    for a in actions:
        if a.player is None:
            continue
        name = a.player.name
        s = stats.setdefault(name, {
            "folds": 0, "checks": 0, "calls": 0,
            "bets": 0, "raises": 0, "all_ins": 0,
            "total_blinds": 0, "net_winnings": 0,
            "showdowns": 0, "hands": set(),
        })
        s["hands"].add(a.hand_id)
        if a.kind == "blind":
            s["total_blinds"] += a.amount or 0
        elif a.kind in ("fold", "vacation_fold"):
            s["folds"] += 1
        elif a.kind == "check":
            s["checks"] += 1
        elif a.kind == "call":
            s["calls"] += 1
        elif a.kind == "bet":
            s["bets"] += 1
        elif a.kind == "raise":
            s["raises"] += 1
        elif a.kind == "all_in":
            s["all_ins"] += 1

    # Net winnings from ending bankrolls
    for h in hands.filter(is_over=True):
        for seat in h.seats.all():
            if seat.bankroll_start is not None and seat.bankroll_end is not None:
                delta = seat.bankroll_end - seat.bankroll_start
                if delta != 0:
                    stats.setdefault(seat.player.name, {
                        "folds": 0, "checks": 0, "calls": 0,
                        "bets": 0, "raises": 0, "all_ins": 0,
                        "total_blinds": 0, "net_winnings": 0,
                        "showdowns": 0, "hands": set(),
                    })["net_winnings"] += delta

    # Count showdowns: hands where the player's status indicates they were in the hand at the end
    # We count a hand as a showdown for a player if they didn't fold and the hand went to showdown
    for h in hands.filter(is_over=True, winner__contains="with"):
        # Simplified: count hands where player was in the hand (had a seat)
        # A more accurate count would need showdown cards, but this is close enough
        for seat in h.seats.filter(status__contains="with"):
            stats.setdefault(seat.player.name, {
                "folds": 0, "checks": 0, "calls": 0,
                "bets": 0, "raises": 0, "all_ins": 0,
                "total_blinds": 0, "net_winnings": 0,
                "showdowns": 0, "hands": set(),
            })["showdowns"] += 1

    n = 0
    for name, s in stats.items():
        hands_played = len(s["hands"])
        if hands_played < 1:
            continue
        total_actions = (s["folds"] + s["checks"] + s["calls"]
                         + s["bets"] + s["raises"] + s["all_ins"])
        fold_rate = s["folds"] / total_actions if total_actions > 0 else 0.5
        aggr = (s["bets"] + s["raises"] + s["all_ins"]) / total_actions if total_actions > 0 else 0.5

        classification = _classify(hands_played, fold_rate, aggr)

        profile, created = PlayerProfile.objects.update_or_create(
            table=table,
            player=_player(name),
            defaults={
                "hands_played": hands_played,
                "folds": s["folds"],
                "checks": s["checks"],
                "calls": s["calls"],
                "bets": s["bets"],
                "raises": s["raises"],
                "all_ins": s["all_ins"],
                "total_blinds": s["total_blinds"],
                "net_winnings": s["net_winnings"],
                "showdowns": s["showdowns"],
                "classification": classification,
            },
        )
        n += 1

    # Delete profiles for players who no longer appear at this table
    PlayerProfile.objects.filter(table=table).exclude(
        player__name__in=stats.keys()
    ).delete()

    return n


def get_profile(table_name: str, player_name: str) -> PlayerProfile | None:
    """Get the profile for one player at one table."""
    try:
        return PlayerProfile.objects.get(
            table__name=table_name, player__name=player_name
        )
    except PlayerProfile.DoesNotExist:
        return None
