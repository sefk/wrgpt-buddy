import re

from django import template
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

register = template.Library()

SUITS = {"s": ("♠", "spade"), "h": ("♥", "heart"), "d": ("♦", "diamond"), "c": ("♣", "club")}
RANKS = {"T": "10"}
CARD_RE = re.compile(r"\b([2-9TJQKA])([shdc])\b")


@register.simple_tag
def card(code):
    """One card as a small face-up tile; empty/unknown renders a face-down slot."""
    m = CARD_RE.fullmatch((code or "").strip())
    if not m:
        return mark_safe('<span class="card empty" aria-label="no card"></span>')
    rank, suit = m.groups()
    sym, name = SUITS[suit]
    shown = RANKS.get(rank, rank)
    return format_html(
        '<span class="card {}" aria-label="{} of {}s"><b>{}</b><i>{}</i></span>',
        name, shown, name, shown, sym,
    )


@register.simple_tag
def cards(text):
    codes = CARD_RE.findall(text or "")
    if not codes:
        return ""
    return format_html(
        '<span class="cards">{}</span>',
        format_html_join("", "{}", ((card(r + s),) for r, s in codes)),
    )


@register.simple_tag
def board(text, slots=5):
    codes = [r + s for r, s in CARD_RE.findall(text or "")]
    codes += [""] * (slots - len(codes))
    return format_html(
        '<span class="cards board">{}</span>',
        format_html_join("", "{}", ((card(c),) for c in codes[:slots])),
    )


@register.filter
def money(value):
    if value is None or value == "":
        return "–"
    return f"${value:,}"


@register.filter
def signed_money(value):
    if value is None:
        return "–"
    return f"{'+' if value > 0 else '-' if value < 0 else ''}${abs(value):,}"


@register.filter
def inline_cards(text):
    """Replace card codes in free text with compact colored glyphs."""

    def repl(m):
        sym, name = SUITS[m.group(2)]
        return f'<span class="ic {name}">{RANKS.get(m.group(1), m.group(1))}{sym}</span>'

    from django.utils.html import escape

    return mark_safe(CARD_RE.sub(repl, str(escape(text or ""))))


@register.filter
def relative(dt):
    """'in 3h 12m' / '45m ago'."""
    if not dt:
        return ""
    secs = int((dt - timezone.now()).total_seconds())
    past = secs < 0
    secs = abs(secs)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        out = f"{d}d {h}h"
    elif h:
        out = f"{h}h {m}m"
    else:
        out = f"{m}m"
    return f"{out} ago" if past else f"in {out}"


@register.inclusion_tag("poker/_profile_legend.html")
def profile_legend():
    from ..state import MIN_HANDS_FOR_BADGE, PROFILE_TYPES

    return {"types": PROFILE_TYPES, "min_hands": MIN_HANDS_FOR_BADGE}
