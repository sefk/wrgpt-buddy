"""Ask a local model on LM Studio's OpenAI-compatible server for a recommendation."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from django.conf import settings

from .prompt import RECOMMENDATION_SCHEMA

SYSTEM = "You are an expert no-limit hold'em coach. Reply only with the requested JSON."


def server_up() -> bool:
    try:
        with urllib.request.urlopen(f"{settings.ADVICE_LMSTUDIO_URL}/models", timeout=5):
            return True
    except (urllib.error.URLError, OSError):
        return False


def ensure_server(log=None) -> bool:
    """LM Studio can be running with models loaded while its API server is off
    (e.g. after the app restarts). Start the server if needed; True if it's up."""
    import subprocess

    if server_up():
        return True
    if log:
        log.warning("LM Studio server not reachable; running `lms server start`")
    try:
        subprocess.run([settings.ADVICE_LMS_BIN, "server", "start"],
                       capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if log:
            log.warning("lms server start failed: %s", exc)
        return False
    return server_up()


def _post(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:400]
        raise RuntimeError(f"LM Studio HTTP {e.code}: {body}") from e


def _parse(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S).strip()
    try:
        return json.loads(content)
    except ValueError:
        m = re.search(r"\{.*\}", content, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def ask(model: str, label: str, prompt: str) -> dict:
    out = {"model": model, "label": label, "action": "", "amount": None, "command": "",
           "reasoning": "", "error": "", "seconds": 0.0}
    t0 = time.monotonic()
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 16000,  # reasoning models spend many tokens before the JSON
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "recommendation", "strict": True, "schema": RECOMMENDATION_SCHEMA},
        },
    }
    try:
        resp = _post(settings.ADVICE_LMSTUDIO_URL.rstrip("/") + "/chat/completions",
                     payload, settings.ADVICE_LOCAL_TIMEOUT_S)
        msg = resp["choices"][0]["message"]
        # Reasoning models served by LM Studio sometimes put the answer in reasoning_content.
        data = _parse(msg.get("content") or msg.get("reasoning_content"))
        out["action"] = data["action"]
        out["amount"] = data.get("amount")
        out["command"] = data.get("command", "")
        out["reasoning"] = data.get("reasoning", "")
    except Exception as e:  # noqa: BLE001 - recorded, never raised
        out["error"] = f"{type(e).__name__}: {e}"
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out
