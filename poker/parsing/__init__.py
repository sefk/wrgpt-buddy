"""Pure parsers for WRGPT dealer emails."""

from __future__ import annotations

import email
import email.policy
import html
import re
from collections.abc import Iterable

from poker.parsing.dealer import parse_advance_ack, parse_dealer_error
from poker.parsing.envelope import parse_meta
from poker.parsing.private import parse_my_reply, parse_private
from poker.parsing.types import (
    AdvanceAck,
    DealerError,
    Board,
    Broadcast,
    Card,
    EmailKind,
    EmailMeta,
    EventKind,
    HandEvent,
    MyReply,
    ParsedEmail,
    PrivateState,
    SeatRow,
)

__all__ = [
    "AdvanceAck", "DealerError", "parse_advance_ack", "parse_dealer_error",
    "Board", "Broadcast", "Card", "EmailKind", "EmailMeta", "EventKind", "HandEvent",
    "MyReply", "ParsedEmail", "PrivateState", "SeatRow",
    "parse_email", "parse_meta", "parse_private", "parse_my_reply",
]


def _body_text(msg) -> str:
    part = msg.get_body(preferencelist=("plain",))
    if part is not None:
        return part.get_content()
    part = msg.get_body(preferencelist=("html",))
    if part is not None:
        h = part.get_content()
        h = re.sub(r"(?is)<(script|style).*?</\1>", "", h)
        h = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li)>", "\n", h)
        return html.unescape(re.sub(r"<[^>]+>", "", h))
    return ""


def parse_email(raw: bytes, my_addresses: Iterable[str] = ("sefklon@gmail.com",)) -> ParsedEmail:
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        meta = parse_meta(msg, my_addresses)
    except Exception as e:  # noqa: BLE001 - never raise on malformed mail
        meta = EmailMeta("", "", "", (), None, None, EmailKind.UNKNOWN)
        return ParsedEmail(meta=meta, errors=(f"envelope: {type(e).__name__}: {e}",))
    if meta.kind is EmailKind.UNKNOWN:
        return ParsedEmail(meta=meta)
    try:
        text = _body_text(msg)
        if meta.kind is EmailKind.BROADCAST:
            from poker.parsing.broadcast import parse_broadcast

            body = parse_broadcast(meta.subject, text)
        elif meta.kind is EmailKind.ADVANCE_ACK:
            body = parse_advance_ack(meta.subject, text)
        elif meta.kind is EmailKind.DEALER_ERROR:
            body = parse_dealer_error(meta.subject, text)
        elif meta.kind is EmailKind.MY_REPLY:
            body = parse_my_reply(meta.subject, text)
        else:
            body = parse_private(meta.subject, text)
    except Exception as e:  # noqa: BLE001
        return ParsedEmail(meta=meta, body=None, errors=(f"{meta.kind.value}: {type(e).__name__}: {e}",))
    return ParsedEmail(meta=meta, body=body)
