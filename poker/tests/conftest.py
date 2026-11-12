from pathlib import Path

import pytest
from django.core.management import call_command

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.django_db


@pytest.fixture
def imported():
    call_command("import_eml", str(FIXTURES))


@pytest.fixture(autouse=True)
def _no_real_lm_studio(monkeypatch, settings):
    """Never probe or start the real LM Studio server, or call OpenRouter, from tests."""
    settings.OPENROUTER_API_KEY = ""
    monkeypatch.setattr("poker.advice.local.server_up", lambda: True)
