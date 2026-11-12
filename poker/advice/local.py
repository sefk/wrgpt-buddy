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


def loaded_models() -> set[str] | None:
    """Identifiers of models currently loaded in LM Studio; None if `lms ps` fails."""
    import subprocess

    try:
        r = subprocess.run([settings.ADVICE_LMS_BIN, "ps", "--json"],
                           capture_output=True, text=True, timeout=30)
        return {m["identifier"] for m in json.loads(r.stdout)}
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return None


def ensure_model(model: str, log=None) -> str:
    """Load `model` explicitly if it isn't loaded (JIT loads right after a reboot
    have stalled for the whole request timeout). Returns "" or an error message.

    `lms load` rejects variant keys like "qwen/x@4bit" and has no variant flag, so
    load the base key (its selected variant) under the full name as identifier."""
    import subprocess

    if model in (loaded_models() or ()):
        return ""
    if log:
        log.warning("%s not loaded; running `lms load`", model)
    try:
        r = subprocess.run(
            [settings.ADVICE_LMS_BIN, "load", model.split("@", 1)[0], "--identifier", model,
             "--context-length", str(settings.ADVICE_LOCAL_CONTEXT),
             "--ttl", str(settings.ADVICE_LOCAL_TTL_S), "-y"],
            capture_output=True, text=True, timeout=settings.ADVICE_LOCAL_LOAD_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"lms load {model} failed: {type(exc).__name__}: {exc}"
    if r.returncode != 0:
        return f"lms load {model} failed: {(r.stderr or r.stdout).strip()[-300:]}"
    return ""


def _post(url: str, payload: dict, timeout: float, headers: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
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


def run(model: str, label: str, prompt: str, log=None) -> dict:
    """ensure_model, then ask; a failed load is recorded like any other error."""
    t0 = time.monotonic()
    err = ensure_model(model, log)
    if err:
        out = _blank(model, label)
        out["error"], out["seconds"] = err, round(time.monotonic() - t0, 1)
        return out
    return ask(model, label, prompt)


def run_remote(model: str, label: str, prompt: str) -> dict:
    """Ask a model hosted on OpenRouter (same chat API as LM Studio, plus a key and a cost)."""
    if not settings.OPENROUTER_API_KEY:
        out = _blank(model, label)
        out["error"] = "OPENROUTER_API_KEY is not set"
        return out
    return ask(model, label, prompt, remote=True)


def ask(model: str, label: str, prompt: str, remote: bool = False) -> dict:
    out = _blank(model, label)
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
        if remote:
            payload["usage"] = {"include": True}  # OpenRouter reports the cost of the call
            resp = _post(settings.OPENROUTER_URL.rstrip("/") + "/chat/completions", payload,
                         settings.ADVICE_REMOTE_TIMEOUT_S,
                         {"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"})
            out["cost"] = float((resp.get("usage") or {}).get("cost") or 0)
        else:
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
