"""Ask the local model, Qwen3.8 Flash-Next on the ds4 server, for a recommendation.

ds4 (~/src/bench runs it as a LaunchDaemon on :8765) keeps the model resident, so
there is nothing to start or load here: a request either gets an answer or an
error that is recorded like any other.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from django.conf import settings

from .prompt import RECOMMENDATION_SCHEMA

SYSTEM = "You are an expert no-limit hold'em coach. Reply only with the requested JSON."


def _post(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:400]
        raise RuntimeError(f"HTTP {e.code} from {url.split('/v1')[0]}: {body}") from e


def _parse(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S).strip()
    try:
        return json.loads(content)
    except ValueError:
        m = re.search(r"\{.*\}", content, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _blank(model: str, label: str) -> dict:
    return {"model": model, "label": label, "action": "", "amount": None, "command": "",
            "reasoning": "", "error": "", "seconds": 0.0}


def ask(model: str, label: str, prompt: str) -> dict:
    out = _blank(model, label)
    t0 = time.monotonic()
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 16000,  # room for the thinking that comes before the JSON
        "reasoning_effort": settings.ADVICE_LOCAL_REASONING_EFFORT,
        # ds4 ignores response_format (the prompt spells the fields out and _parse
        # digs the JSON out of the reply); other OpenAI-compatible servers honour it.
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "recommendation", "strict": True, "schema": RECOMMENDATION_SCHEMA},
        },
    }
    try:
        resp = _post(settings.ADVICE_LOCAL_URL.rstrip("/") + "/chat/completions",
                     payload, settings.ADVICE_LOCAL_TIMEOUT_S)
        choice = resp["choices"][0]
        msg = choice["message"]
        if not (msg.get("content") or "").strip() and choice.get("finish_reason") == "length":
            raise RuntimeError("thought through the whole token budget without answering")
        # Some servers put a reasoning model's answer in reasoning_content.
        data = _parse(msg.get("content") or msg.get("reasoning_content"))
        out["action"] = data["action"]
        out["amount"] = data.get("amount")
        out["command"] = data.get("command", "")
        out["reasoning"] = data.get("reasoning", "")
    except Exception as e:  # noqa: BLE001 - recorded, never raised
        out["error"] = f"{type(e).__name__}: {e}"
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out
