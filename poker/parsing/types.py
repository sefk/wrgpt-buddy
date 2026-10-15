"""Parsed representations of WRGPT dealer emails.

These are the contract between the parsers (pure functions over email bytes)
and the state layer (which applies them to the database). Parsers never touch
the database; the state layer never looks at raw email text.

Conventions:
- Money is an int number of dollars ("$14,650" -> 14650).
- Cards are two-char strings, rank then suit: "As", "Th", "9d".
- Board is a tuple of five entries, None for an undealt "?".
- Datetimes are timezone-aware, in the dealer's zone (America/Los_Angeles).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime

Card = str
Board = tuple[Card | None, Card | None, Card | None, Card | None, Card | None]


class EmailKind(enum.StrEnum):
    BROADCAST = "broadcast"  # "[b17.10014][hand:2] Holdem Game Broadcast Message(-> X)"
    YOUR_CARDS = "your_cards"  # "Holdem game (ID=pikaro) [hand:2]: your cards"
    REMINDER = "reminder"  # dealer's "reminder (ID=vimuho)[hand:2]"
    MY_REPLY = "my_reply"  # my own reply to the dealer, e.g. "Re: reminder (ID=...)"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EmailMeta:
    message_id: str
    subject: str
    from_addr: str  # bare address, lowercased
    to_addrs: tuple[str, ...]
    date: datetime | None
    in_reply_to: str | None
    kind: EmailKind


class EventKind(enum.StrEnum):
    NEW_HAND = "new_hand"  # "Dealing a new hand"
    ANTE = "ante"  # "No ante" (amount None) or an ante line
    BLIND = "blind"  # "Two_Jacks blinds $100"
    VACATION_FOLD = "vacation_fold"  # "Options Dee is on vacation and folds"
    BACK_FROM_VACATION = "back_from_vacation"  # "RiverRat is back from vacation"
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"  # amount = bet size
    RAISE = "raise"  # amount = raise increment, total = "to $X total"
    ALL_IN = "all_in"
    POT_RIGHT = "pot_right"  # "Pot right ($3450), flopping/dealing/drawing cards"; amount = pot
    PLAYERS_LEFT = "players_left"  # "5 players"; amount = count
    BOARD_CARDS = "board_cards"  # "Flopped cards: As 6c Th" / "Flopped card: 4c"; cards
    HAND_OVER = "hand_over"  # "Hand over, no showdown"
    SHOWDOWN_BOARD = "showdown_board"  # "Hand over, current board is: ..." / "Current board is: ..."; cards
    SHOWS = "shows"  # "Bryan Currie has: Kh Jh"; player, cards
    WINS = "wins"  # "Capparella wins $14,650 (net $9100) with One Pair (...)"; amount, net, text
    TIMED_OUT = "timed_out"  # "Aardvark timed out during this hand"
    OTHER = "other"  # anything unrecognised; text preserved


@dataclass(frozen=True)
class HandEvent:
    kind: EventKind
    text: str  # the original line, stripped of the leading "! " and timestamp
    timestamp: datetime | None = None  # present only in "History of this hand" lines
    player: str | None = None
    amount: int | None = None
    total: int | None = None  # raise-to total
    net: int | None = None  # WINS only
    cards: tuple[Card, ...] = ()


@dataclass(frozen=True)
class SeatRow:
    """One row of the +-+ status table."""

    seat: int
    name: str
    bankroll: int
    action: int | None  # chips put in this hand; None when blank (after hand over)
    status: str  # "", "folded", "NoSho", ... as printed
    is_dealer: bool  # "D" marker
    is_next: bool  # ">" marker
    is_vacation: bool  # "V" marker
    pot_number: str = ""
    pot_size: int | None = None


@dataclass(frozen=True)
class Broadcast:
    table: str  # "b17"
    hand_number: int
    day: int
    sequence: int  # the 10014 in "[b17.10014]"
    addressed_to: str | None  # name after "->" in the subject; None for "(*)"
    events: tuple[HandEvent, ...]  # the new lines at the top of this message
    history: tuple[HandEvent, ...]  # "History of this hand" (final message), else ()
    next_to_act: str | None
    to_call: int | None
    pot: int | None
    next_timeout: datetime | None
    betting_round: int | None
    board: Board | None
    seats: tuple[SeatRow, ...]  # first status table
    final_seats: tuple[SeatRow, ...]  # status table after "Hand over", else ()
    hand_over: bool
    notes: tuple[str, ...] = ()  # trailing "!" lines after the final table
    status_url: str | None = None


@dataclass(frozen=True)
class PrivateState:
    """My private view of a hand: from "your cards" or a reminder."""

    kind: EmailKind  # YOUR_CARDS or REMINDER
    game_id: str | None  # the ID=... token; changes between messages
    table: str
    hand_number: int
    day: int | None
    hole_cards: tuple[Card, Card]
    to_call: int | None = None
    pot: int | None = None
    board: Board | None = None
    next_timeout: datetime | None = None
    start_time: int | None = None
    status_url: str | None = None


@dataclass(frozen=True)
class MyReply:
    """An email I sent to the dealer (e.g. replying "Fold" to a reminder)."""

    command: str  # first non-empty line I typed, e.g. "Fold", "call"
    hand_number: int | None
    game_id: str | None
    quoted: PrivateState | None  # the dealer message I was replying to, if quoted


@dataclass(frozen=True)
class ParsedEmail:
    meta: EmailMeta
    body: Broadcast | PrivateState | MyReply | None = None
    errors: tuple[str, ...] = field(default=())
