"""Sonnet via the `claude` CLI: turn 1 recommends, turn 2 (same session) judges.

Both turns must pass identical --model/--json-schema/--tools/--setting-sources/
--strict-mcp-config/--system-prompt flags: structured output is a tool, so any
difference changes the cached prefix and the second turn misses the cache.
"""

from __future__ import annotations

import json
import subprocess
import time

from django.conf import settings

from .prompt import RECOMMENDATION_SCHEMA

CONCURRENCE = ["agree", "same_action_different_size", "different_action"]

_judgement_props = {
    "verdicts": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "concurrence": {"type": "string", "enum": CONCURRENCE},
                "evaluation": {"type": "string"},
            },
            "required": ["model", "concurrence", "evaluation"],
            "additionalProperties": False,
        },
    },
    "preferred": {"type": "string"},
    "summary": {"type": "string"},
    "on_actual": {"type": "string"},
}
JUDGEMENT_SCHEMA = {
    "type": "object",
    "properties": _judgement_props,
    "required": list(_judgement_props),
    "additionalProperties": False,
}
# One schema for both turns (see module docstring); the unused half is null.
COMBINED_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendation": {"anyOf": [RECOMMENDATION_SCHEMA, {"type": "null"}]},
        "judgement": {"anyOf": [JUDGEMENT_SCHEMA, {"type": "null"}]},
    },
    "required": ["recommendation", "judgement"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are an expert no-limit hold'em coach advising one player in a slow email tournament. "
    "You always answer with the structured output. When asked for advice on a situation, fill "
    "`recommendation` and set `judgement` to null. When later asked to compare other models' "
    "answers with your own, fill `judgement` and set `recommendation` to null. In `judgement`, "
    "`on_actual` comments on the move the player actually made when you are told it; "
    "otherwise it is an empty string."
)


AUTH_HINT = (
    "The claude CLI is not logged in for background use. Run `claude setup-token` and put "
    "the token in .env as CLAUDE_CODE_OAUTH_TOKEN, then `bin/deploy`."
)


def _explain(msg: str) -> str:
    return f"{msg} — {AUTH_HINT}" if "authenticate" in msg.lower() else msg


def is_auth_error(err: str) -> bool:
    return AUTH_HINT in (err or "")


class ClaudeError(Exception):
    pass


def _base_cmd():
    s = settings
    return [
        s.ADVICE_CLAUDE_BIN, "-p", "--model", s.ADVICE_CLAUDE_MODEL,
        "--output-format", "json",
        "--json-schema", json.dumps(COMBINED_SCHEMA),
        "--tools", "", "--setting-sources", "", "--strict-mcp-config",
        "--system-prompt", SYSTEM_PROMPT,
    ]


def _invoke(prompt: str, resume: str | None = None) -> dict:
    cmd = _base_cmd()
    if resume:
        cmd += ["--resume", resume]
    cmd.append(prompt)
    cwd = settings.ADVICE_CLAUDE_CWD
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=settings.ADVICE_CLAUDE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ClaudeError(f"claude timed out after {settings.ADVICE_CLAUDE_TIMEOUT_S}s")
    if p.returncode != 0:
        try:
            msg = str(json.loads(p.stdout).get("result"))
        except ValueError:
            msg = (p.stderr or p.stdout)[-500:]
        raise ClaudeError(f"claude exited {p.returncode}: {_explain(msg)}")
    try:
        data = json.loads(p.stdout)
    except ValueError:
        raise ClaudeError(f"claude output not JSON: {p.stdout[-300:]}")
    if data.get("is_error"):
        raise ClaudeError(f"claude error: {_explain(str(data.get('result'))[:500])}")
    if not isinstance(data.get("structured_output"), dict):
        raise ClaudeError(f"no structured_output: {str(data.get('result'))[:300]}")
    return data


def recommend(prompt: str) -> tuple[dict, str | None, float]:
    """Turn 1. Returns (opinion, session_id, cost_usd)."""
    out = {"model": settings.ADVICE_CLAUDE_MODEL, "label": "Sonnet", "action": "", "amount": None,
           "command": "", "reasoning": "", "error": "", "seconds": 0.0, "grade": ""}
    t0 = time.monotonic()
    session, cost = None, 0.0
    try:
        data = _invoke(prompt)
        session = data.get("session_id")
        cost = float(data.get("total_cost_usd") or 0)
        rec = data["structured_output"].get("recommendation")
        if not rec:
            raise ClaudeError("structured output had no recommendation")
        out.update(action=rec["action"], amount=rec.get("amount"),
                   command=rec.get("command", ""), reasoning=rec.get("reasoning", ""))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out, session, cost


def _fmt(o):
    amt = f" ${o['amount']}" if o.get("amount") is not None else ""
    return f"{o['action']}{amt} (command: {o.get('command', '')})\n{o.get('reasoning', '')}"


def judge(session_id: str, locals_: list[dict], to_call=None, actual: dict | None = None) -> tuple[dict, float]:
    """Turn 2 in the same session. Returns (judgement, cost_usd); judgement has `error` on failure."""
    ok = [o for o in locals_ if not o.get("error")]
    failed = [o["label"] for o in locals_ if o.get("error")]
    parts = ["Now compare other models' answers to the same situation with your own recommendation.", ""]
    for o in ok:
        parts += [f"{o['label']} answered: {_fmt(o)}", ""]
    if failed:
        parts += [f"({', '.join(failed)} failed to answer; ignore them.)", ""]
    if actual:
        amt = f" (total ${actual['amount']:,} this street)" if actual.get("amount") is not None else ""
        parts += [
            f"The player actually played: {actual.get('text') or actual.get('action')}{amt}. "
            "This was a past decision and the player has since seen how the hand went; you have not. "
            "In `on_actual`, give your candid, specific comment on that move (2-5 sentences): was it right, "
            "wrong, or close, and why? You may conclude the player was right and the models were wrong.",
            "",
        ]
    else:
        parts += ["Set `on_actual` to an empty string.", ""]
    parts += [
        "For each model above, add a verdict: concurrence is 'agree' (same action, similar size), "
        "'same_action_different_size', or 'different_action'; the evaluation explains why. "
        "You may side with a local model if its answer is better than yours; when answers differ, "
        "be specific about why one is right (pot odds, position, ranges, stack depth). "
        f"Use the model labels {', '.join(repr(o['label']) for o in ok)} in `model`. "
        "`preferred` is the label of the best answer overall: 'Sonnet' or one of those labels. "
        "`summary` is 2-4 sentences. Fill `judgement` and set `recommendation` to null.",
    ]
    t0 = time.monotonic()
    try:
        data = _invoke("\n".join(parts), resume=session_id)
        j = dict(data["structured_output"].get("judgement") or {})
        if not j:
            raise ClaudeError("structured output had no judgement")
        j["cache_read_tokens"] = (data.get("usage") or {}).get("cache_read_input_tokens")
        j["seconds"] = round(time.monotonic() - t0, 1)
        return j, float(data.get("total_cost_usd") or 0)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}", "seconds": round(time.monotonic() - t0, 1)}, 0.0
