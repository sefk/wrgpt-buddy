import pytest


@pytest.fixture(autouse=True)
def _no_real_lm_studio(monkeypatch):
    """Never probe or start the real LM Studio server from tests."""
    monkeypatch.setattr("poker.advice.local.server_up", lambda: True)
