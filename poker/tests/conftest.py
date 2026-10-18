import pytest


@pytest.fixture(autouse=True)
def _no_real_lm_studio(monkeypatch, settings):
    """Never probe or start the real LM Studio server, or call OpenRouter, from tests."""
    settings.OPENROUTER_API_KEY = ""
    monkeypatch.setattr("poker.advice.local.server_up", lambda: True)
