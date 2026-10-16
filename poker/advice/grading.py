"""Grade how well a local model's recommendation matches Sonnet's. Pure code."""

from __future__ import annotations

AGREE = "agree"
SIZE = "same_action_different_size"
DIFFERENT = "different_action"
AMOUNT_TOLERANCE = 0.20  # amounts within 20% of the larger count as the same size
_SEVERITY = {AGREE: 0, SIZE: 1, DIFFERENT: 2}


def _family(action, to_call):
    if action in ("bet", "raise", "all_in"):
        return "aggressive"
    if action in ("check", "call"):
        # Checking is calling $0; with money to call they are different things.
        return "pass" if not to_call else action
    return action  # fold


def _amount(op, my_stack):
    if op.get("action") == "all_in":
        return my_stack if my_stack else op.get("amount")
    return op.get("amount")


def grade(local: dict, sonnet: dict, to_call=None, my_stack=None) -> str:
    if local.get("error") or sonnet.get("error") or not local.get("action") or not sonnet.get("action"):
        return ""
    fl, fs = _family(local["action"], to_call), _family(sonnet["action"], to_call)
    if fl != fs:
        return DIFFERENT
    if fl != "aggressive":
        return AGREE
    a, b = _amount(local, my_stack), _amount(sonnet, my_stack)
    if a is None and b is None:
        return AGREE
    if a is None or b is None:
        return SIZE
    if max(a, b) == 0 or abs(a - b) / max(a, b) <= AMOUNT_TOLERANCE:
        return AGREE
    return SIZE


def overall(grades) -> str:
    grades = [g for g in grades if g]
    if not grades:
        return ""
    return max(grades, key=_SEVERITY.__getitem__)
