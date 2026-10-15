"""Email header parsing and classification."""

from __future__ import annotations

import email.message
import email.utils
import re
from collections.abc import Iterable
from datetime import datetime, timezone

from poker.parsing.types import EmailKind, EmailMeta

_BROADCAST = re.compile(r"\[\w+\.\d+\]\s*\[hand:\d+\]\s*Holdem Game Broadcast Message", re.I)
_YOUR_CARDS = re.compile(r"Holdem game \(ID=[^)]+\)\s*\[hand:\d+\]:?\s*your cards", re.I)
_REMINDER = re.compile(r"reminder \(ID=[^)]+\)\s*\[hand:\d+\]", re.I)
_RE_PREFIX = re.compile(r"^\s*re:\s*", re.I)


def bare_addr(value: str | None) -> str:
    return email.utils.parseaddr(str(value or ""))[1].lower()


def _is_dealer_subject(subject: str) -> bool:
    return bool(_BROADCAST.search(subject) or _YOUR_CARDS.search(subject) or _REMINDER.search(subject))


def classify(subject: str, from_addr: str, to_addrs: Iterable[str], my_addresses: Iterable[str]) -> EmailKind:
    mine = {a.lower() for a in my_addresses}
    subject = subject or ""
    if from_addr.endswith("@wrgpt.org"):
        if _BROADCAST.search(subject):
            return EmailKind.BROADCAST
        if _YOUR_CARDS.search(subject):
            return EmailKind.YOUR_CARDS
        if _REMINDER.search(subject):
            return EmailKind.REMINDER
        return EmailKind.UNKNOWN
    if from_addr in mine:
        to_dealer = any(a.endswith("@wrgpt.org") for a in to_addrs)
        re_dealer = bool(_RE_PREFIX.match(subject)) and _is_dealer_subject(_RE_PREFIX.sub("", subject))
        if to_dealer or re_dealer:
            return EmailKind.MY_REPLY
    return EmailKind.UNKNOWN


def parse_meta(msg: email.message.EmailMessage, my_addresses: Iterable[str]) -> EmailMeta:
    subject = " ".join(str(msg["Subject"] or "").split())
    from_addr = bare_addr(msg["From"])
    to_addrs = tuple(a.lower() for _, a in email.utils.getaddresses([str(h) for h in msg.get_all("To", [])]) if a)
    date = None
    raw_date = msg["Date"]
    if raw_date:
        try:
            date = email.utils.parsedate_to_datetime(str(raw_date))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            date = None
    irt = msg["In-Reply-To"]
    return EmailMeta(
        message_id=str(msg["Message-ID"] or "").strip(),
        subject=subject,
        from_addr=from_addr,
        to_addrs=to_addrs,
        date=date,
        in_reply_to=str(irt).strip() if irt else None,
        kind=classify(subject, from_addr, to_addrs, my_addresses),
    )
