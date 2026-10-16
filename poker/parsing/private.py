"""Parsers for the dealer's private emails ("your cards", "reminder") and my replies."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from poker.parsing.types import Board, EmailKind, MyReply, PrivateState

LA = ZoneInfo("America/Los_Angeles")

_CARD = r"[2-9TJQKA][shdc]"
_GAME_ID = re.compile(r"\bID=([^)\s\]]+)")  # "(ID=x)" from the dealer, bare "ID=x" from me
_SUBJ_HAND = re.compile(r"\[hand:(\d+)\]")
_TABLE = re.compile(r"^!?\s*Table (\w+), Hand (\d+)(?:, Day (\d+))?", re.M)
_HOLE = re.compile(rf"Your hole cards are:?\s+({_CARD})\s+({_CARD})")
_TO_CALL = re.compile(r"\$([\d,]+) to call")
_POT = re.compile(r"Pot size:\s*\$([\d,]+)")
_BOARD = re.compile(r"^!?\s*Board:\s*(.+)$", re.M)
_TIMEOUT = re.compile(r"Next timeout set for \w+, (\d+ \w+ \d{4} \d+:\d+)")
_START = re.compile(r"Your START time is:\s*(\d+)")
_URL = re.compile(r"current status can be found at\s+(https?://\S+)")


def _money(s: str) -> int:
    return int(s.replace(",", ""))


def _board(text: str) -> Board | None:
    m = _BOARD.search(text)
    if not m:
        return None
    toks = m.group(1).split()
    if len(toks) != 5:
        return None
    return tuple(None if t == "?" else t for t in toks)  # type: ignore[return-value]


def _timeout(text: str) -> datetime | None:
    m = _TIMEOUT.search(text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d %b %Y %H:%M").replace(tzinfo=LA)
    except ValueError:
        return None


def parse_private(subject: str, body: str) -> PrivateState:
    """Parse a dealer "your cards" or "reminder" message. Raises ValueError if malformed."""
    subj_l = subject.lower()
    kind = EmailKind.REMINDER if "reminder" in subj_l or "Reminder:" in body else EmailKind.YOUR_CARDS
    gm = _GAME_ID.search(subject)
    tm = _TABLE.search(body)
    if not tm:
        raise ValueError("no 'Table X, Hand N' line found")
    hm = _HOLE.search(body)
    if not hm:
        raise ValueError("no hole cards found")
    sh = _SUBJ_HAND.search(subject)
    hand = int(sh.group(1)) if sh else int(tm.group(2))
    tc, pot, st, url = _TO_CALL.search(body), _POT.search(body), _START.search(body), _URL.search(body)
    return PrivateState(
        kind=kind,
        game_id=gm.group(1) if gm else None,
        table=tm.group(1),
        hand_number=hand,
        day=int(tm.group(3)) if tm.group(3) else None,
        hole_cards=(hm.group(1), hm.group(2)),
        to_call=_money(tc.group(1)) if tc else None,
        pot=_money(pot.group(1)) if pot else None,
        board=_board(body),
        next_timeout=_timeout(body),
        start_time=int(st.group(1)) if st else None,
        status_url=url.group(1) if url else None,
    )


def parse_my_reply(subject: str, body: str) -> MyReply:
    """Parse an email I sent to the dealer; quoted dealer text is parsed if present."""
    command = ""
    quoted_lines: list[str] = []
    for line in body.splitlines():
        if line.lstrip().startswith(">"):
            quoted_lines.append(re.sub(r"^\s*>\s?", "", line))
        elif not command and line.strip() and not quoted_lines:
            command = line.strip()
    gm = _GAME_ID.search(subject)
    sh = _SUBJ_HAND.search(subject)
    quoted = None
    if quoted_lines:
        try:
            quoted = parse_private(subject, "\n".join(quoted_lines))
        except ValueError:
            quoted = None
    return MyReply(
        command=command,
        hand_number=int(sh.group(1)) if sh else (quoted.hand_number if quoted else None),
        game_id=gm.group(1) if gm else None,
        quoted=quoted,
    )
