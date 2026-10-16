"""Parser for the dealer's "Holdem Game Broadcast Message" emails.

Pure function over the decoded subject and text/plain body; stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from .types import Board, Broadcast, EventKind, HandEvent, SeatRow

TZ = ZoneInfo("America/Los_Angeles")

_SUBJECT_RE = re.compile(r"\[(?P<table>[^.\]]+)\.(?P<seq>\d+)\]\[hand[:_](?P<hand>\d+)\]")
_ADDR_RE = re.compile(r"\(\s*->\s*(?P<to>.*?)\s*\)\s*$")
_HEADER_RE = re.compile(r"^!\s*Table\s+(?P<table>\S+?),\s*Hand\s+(?P<hand>\d+),\s*Day\s+(?P<day>\d+)")
_HIST_TS_RE = re.compile(r"^(\d\d/\d\d/\d\d \d\d:\d\d:\d\d)!\s?(.*)$")
_ROW_RE = re.compile(r"^\s*\d+\|")
_URL_RE = re.compile(r"current status can be found at\s+(https?://\S+)")  # never javascript: etc.

_MONEY = r"\$(-?[\d,]+)"
_NAME = r"(?P<p>.+?)"


def _money(s: str) -> int:
    return int(s.replace(",", ""))


def _cards(s: str) -> tuple[str, ...]:
    return tuple(t for t in s.split() if t != "?")


# (regex, kind, builder) -- tried in order against the stripped event text.
def _ev(kind, text, ts, m=None, **kw):
    return HandEvent(kind=kind, text=text, timestamp=ts, **kw)


def parse_event(text: str, ts: datetime | None = None) -> HandEvent | None:
    """Classify one action/event line (without leading '! ' or timestamp).

    Returns None for status lines that are not events.
    """
    text = text.strip()
    if text.endswith(" and is all in"):
        text = text[: -len(" and is all in")]
        ev = parse_event(text, ts)
        if ev and ev.kind != EventKind.OTHER:
            return replace(ev, text=f"{text} and is all in", all_in=True)
        # e.g. "X4 raises $4950 and is all in" (no "to $N total"): keep the actor
        if m := re.match(rf"^{_NAME} (?:raises|bets|calls)\b(?:.*?{_MONEY})?", text):
            amt = _money(m[2]) if m[2] else None
            return _ev(EventKind.ALL_IN, f"{text} and is all in", ts, player=m["p"], amount=amt, all_in=True)
        return _ev(EventKind.ALL_IN, f"{text} and is all in", ts, all_in=True)
    if text == "Dealing a new hand":
        return _ev(EventKind.NEW_HAND, text, ts)
    if text == "No ante":
        return _ev(EventKind.ANTE, text, ts)
    if m := re.match(rf"^{_NAME} antes {_MONEY}$", text):
        return _ev(EventKind.ANTE, text, ts, player=m["p"], amount=_money(m[2]))
    if m := re.match(rf"^{_NAME} blinds {_MONEY}$", text):
        return _ev(EventKind.BLIND, text, ts, player=m["p"], amount=_money(m[2]))
    if m := re.match(rf"^{_NAME} is on vacation and folds$", text):
        return _ev(EventKind.VACATION_FOLD, text, ts, player=m["p"])
    if m := re.match(rf"^{_NAME} is back from vacation$", text):
        return _ev(EventKind.BACK_FROM_VACATION, text, ts, player=m["p"])
    if m := re.match(rf"^{_NAME} folds$", text):
        return _ev(EventKind.FOLD, text, ts, player=m["p"])
    if m := re.match(rf"^{_NAME} checks$", text):
        return _ev(EventKind.CHECK, text, ts, player=m["p"])
    if m := re.match(rf"^{_NAME} calls(?: {_MONEY})?$", text):
        amt = _money(m[2]) if m[2] else None
        return _ev(EventKind.CALL, text, ts, player=m["p"], amount=amt)
    if m := re.match(rf"^{_NAME} bets {_MONEY}$", text):
        return _ev(EventKind.BET, text, ts, player=m["p"], amount=_money(m[2]))
    if m := re.match(rf"^{_NAME} raises {_MONEY} to {_MONEY} total$", text):
        return _ev(
            EventKind.RAISE, text, ts, player=m["p"], amount=_money(m[2]), total=_money(m[3])
        )
    if m := re.match(rf"^{_NAME} (?:goes |is )?all[- ]in\b.*$", text, re.I):
        am = re.search(_MONEY, text)
        return _ev(EventKind.ALL_IN, text, ts, player=m["p"], amount=_money(am[1]) if am else None)
    if m := re.match(rf"^Pot right \({_MONEY}\)", text):
        return _ev(EventKind.POT_RIGHT, text, ts, amount=_money(m[1]))
    if m := re.match(r"^(\d+) players?$", text):
        return _ev(EventKind.PLAYERS_LEFT, text, ts, amount=int(m[1]))
    if m := re.match(r"^\w+ cards?:\s*(.*)$", text):
        return _ev(EventKind.BOARD_CARDS, text, ts, cards=_cards(m[1]))
    if text == "Hand over, no showdown":
        return _ev(EventKind.HAND_OVER, text, ts)
    if m := re.match(r"^(?:Hand over, current|Current) board is:\s*(.*)$", text):
        return _ev(EventKind.SHOWDOWN_BOARD, text, ts, cards=_cards(m[1]))
    if m := re.match(rf"^{_NAME}\s+has:\s*(.*)$", text):
        return _ev(EventKind.SHOWS, text, ts, player=m["p"], cards=_cards(m[2]))
    if m := re.match(rf"^{_NAME} wins {_MONEY}(?: \(net {_MONEY}\))?", text):
        net = _money(m[3]) if m[3] else None
        return _ev(EventKind.WINS, text, ts, player=m["p"], amount=_money(m[2]), net=net)
    if m := re.match(r"^Player leaving table: (?P<p>.+)$", text):
        return _ev(EventKind.PLAYER_LEFT, text, ts, player=m["p"])
    if m := re.match(r"^-- (?P<p>.+) --$", text):
        return _ev(EventKind.TABLE_TALK, "", ts, player=m["p"])
    if m := re.match(rf"^{_NAME} timed out during this hand$", text):
        return _ev(EventKind.TIMED_OUT, text, ts, player=m["p"])
    # Status-ish lines that are not events.
    if text == "Unlimited raises" or re.match(r"^Checked to .+$", text):
        return None
    return _ev(EventKind.OTHER, text, ts)


def _parse_timeout(s: str) -> datetime | None:
    # "Thu, 15 Oct 2026 08:03 PDT" -> aware datetime in the dealer's zone.
    m = re.match(r"^\w+,\s*(\d+ \w+ \d{4} \d+:\d+)", s.strip())
    if not m:
        return None
    try:
        return datetime.strptime(m[1], "%d %b %Y %H:%M").replace(tzinfo=TZ)
    except ValueError:
        return None


def _parse_board(s: str) -> Board:
    toks = [None if t == "?" else t for t in s.split()][:5]
    toks += [None] * (5 - len(toks))
    return tuple(toks)  # type: ignore[return-value]


def _parse_row(line: str) -> SeatRow | None:
    parts = line.rstrip("\n").split("|")
    if len(parts) < 5:
        return None
    try:
        seat = int(parts[0])
    except ValueError:
        return None
    markers = parts[1][:3]
    name = parts[1][3:].strip()

    def num(s: str) -> int | None:
        s = s.strip().replace(",", "")
        return int(s) if s.lstrip("-").isdigit() else None

    bankroll = num(parts[2])
    pot_size = num(parts[6]) if len(parts) > 6 else None
    return SeatRow(
        seat=seat,
        name=name,
        bankroll=bankroll if bankroll is not None else 0,
        action=num(parts[3]),
        status=parts[4].strip(),
        is_dealer="D" in markers,
        is_next=">" in markers,
        is_vacation="V" in markers,
        pot_number=parts[5].strip() if len(parts) > 5 else "",
        pot_size=pot_size,
    )


def parse_broadcast(subject: str, body: str) -> Broadcast:
    sm = _SUBJECT_RE.search(subject or "")
    if not sm:
        raise ValueError(f"not a broadcast subject: {subject!r}")
    am = _ADDR_RE.search(subject)
    addressed_to = am["to"] if am else None

    lines = body.splitlines()
    header = None
    for ln in lines:
        if ln.strip():
            header = _HEADER_RE.match(ln.strip())
            break
    if not header:
        raise ValueError("broadcast body has no 'Table ..., Hand ..., Day ...' header")

    events: list[HandEvent] = []
    history: list[HandEvent] = []
    notes: list[str] = []
    tables: list[list[SeatRow]] = []
    next_to_act = to_call = pot = betting_round = None
    next_timeout = board = None
    status_url = None
    in_row = False
    mode = "pre"  # pre -> after_table -> history -> notes
    talk: list[str] = []  # pending "% ..." table-talk lines, signed by a later "-- Name --"
    in_rules = False  # between "_____" rules: the history quotes talk there as "! ..." lines

    def _with_talk(ev):
        if ev.kind != EventKind.TABLE_TALK:
            return ev
        text = "\n".join(talk)
        talk.clear()
        return replace(ev, text=text)

    for raw in lines[1:] if lines else []:
        line = raw.rstrip()
        if _ROW_RE.match(line):
            if not in_row:
                tables.append([])
            in_row = True
            row = _parse_row(line)
            if row:
                tables[-1].append(row)
            continue
        if line.startswith("+-+") or line.startswith("|#|"):
            continue
        if in_row:
            in_row = False
            mode = "after_table" if len(tables) == 1 else "notes"
        if m := _URL_RE.search(line):
            status_url = m[1]
            mode = "done"
            continue
        if line.startswith("%") and mode != "done":
            talk.append(line[1:].strip())
            continue
        if line.startswith("____"):
            in_rules = not in_rules
            continue
        if mode == "done" or not line.startswith("!"):
            if line.strip() and mode == "notes":
                mode = "done"
            continue
        text = line[1:].strip()
        if text.startswith("History of this hand"):
            mode = "history"
            continue
        if mode == "notes":
            notes.append(text)
            continue
        if mode == "history":
            if in_rules and not text.startswith("-- "):
                talk.append(text)
                continue
            tm = _HIST_TS_RE.match(text)
            ts = None
            if tm:
                ts = datetime.strptime(tm[1], "%m/%d/%y %H:%M:%S").replace(tzinfo=TZ)
                text = tm[2]
            ev = parse_event(text, ts)
            if ev:
                history.append(_with_talk(ev))
            continue
        # pre / after_table: action lines and status lines
        if m := re.match(r"^(.+?) is (?:next|first) to act(?:, \$([\d,]+) to call)?$", text):
            next_to_act = m[1]
            to_call = _money(m[2]) if m[2] else None
        elif m := re.match(r"^Pot size:\s*\$([\d,]+)", text):
            pot = _money(m[1])
        elif m := re.match(r"^Next timeout set for (.*)$", text):
            next_timeout = _parse_timeout(m[1])
        elif m := re.match(r"^Betting round:\s*(\d+)", text):
            betting_round = int(m[1])
        elif m := re.match(r"^Board:\s*(.*)$", text):
            board = _parse_board(m[1])
        elif (ev := parse_event(text)) is not None:
            events.append(_with_talk(ev))

    return Broadcast(
        table=header["table"],
        hand_number=int(header["hand"]),
        day=int(header["day"]),
        sequence=int(sm["seq"]),
        addressed_to=addressed_to,
        events=tuple(events),
        history=tuple(history),
        next_to_act=next_to_act,
        to_call=to_call,
        pot=pot,
        next_timeout=next_timeout,
        betting_round=betting_round,
        board=board,
        seats=tuple(tables[0]) if tables else (),
        final_seats=tuple(tables[1]) if len(tables) > 1 else (),
        hand_over=len(tables) > 1,
        notes=tuple(notes),
        status_url=status_url,
    )
