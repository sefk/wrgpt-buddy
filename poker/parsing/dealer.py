"""Parsers for the dealer's "Advance action acknowledgement" and "Error Message" emails."""

from __future__ import annotations

import re

from poker.parsing.types import AdvanceAck, DealerError

_GAME_ID = re.compile(r"\(ID=([^)\s]+)\)")
_SUBJ_HAND = re.compile(r"\[hand:(\d+)\]")
_TABLE = re.compile(r"^!?\s*Table (\w+), Hand (\d+)(?:, Day (\d+))?", re.M)
_ACK_KIND = re.compile(r"^(Sticky|Non-sticky) advance action:?\s*$", re.I)
_NO_TALK = re.compile(r"^No table talk stored", re.I)
_ERROR = re.compile(r"^\?\s*Error:\s*(.+)$", re.M)


def _header(subject: str, body: str):
    tm = _TABLE.search(body)
    if not tm:
        raise ValueError("no 'Table X, Hand N' line found")
    sh = _SUBJ_HAND.search(subject)
    gm = _GAME_ID.search(subject)
    hand = int(sh.group(1)) if sh else int(tm.group(2))
    return tm.group(1), hand, int(tm.group(3)) if tm.group(3) else None, gm.group(1) if gm else None


def parse_advance_ack(subject: str, body: str) -> AdvanceAck:
    """Parse "Acknowledging your advance action". Raises ValueError if malformed.

    Body lines (all "!"-prefixed) are: the acknowledgement, "Sticky advance action:" or
    "Non-sticky advance action:", one or more description lines, then either
    "No table talk stored ..." or the table talk text.
    """
    table, hand, day, game_id = _header(subject, body)
    lines = []
    for line in body.splitlines():
        if "current status can be found" in line:
            break  # the dealer's boilerplate footer follows
        if line.startswith("!"):
            lines.append(line[1:].strip())
    idx = next((i for i, ln in enumerate(lines) if _ACK_KIND.match(ln)), None)
    if idx is None:
        raise ValueError("no 'advance action:' line found")
    sticky = lines[idx].lower().startswith("sticky")
    rest = [ln for ln in lines[idx + 1 :] if ln]
    desc, talk = [], []
    seen_talk = False
    for ln in rest:
        if _NO_TALK.match(ln):
            seen_talk = True
        elif seen_talk:
            talk.append(ln)
        else:
            desc.append(ln)
    if not desc:
        raise ValueError("no advance action description found")
    return AdvanceAck(
        table=table, hand_number=hand, day=day, game_id=game_id, sticky=sticky,
        description="\n".join(desc), table_talk="\n".join(talk) or None,
    )


def parse_dealer_error(subject: str, body: str) -> DealerError:
    """Parse the dealer's "Error Message". Raises ValueError if malformed."""
    table, hand, day, game_id = _header(subject, body)
    msgs = _ERROR.findall(body)
    if not msgs:
        raise ValueError("no '? Error:' line found")
    return DealerError(table=table, hand_number=hand, day=day, game_id=game_id, message=" ".join(m.strip() for m in msgs))
