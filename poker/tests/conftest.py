from pathlib import Path

import pytest
from django.core.management import call_command

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.django_db


@pytest.fixture
def imported():
    call_command("import_eml", str(FIXTURES))


@pytest.fixture(autouse=True)
def _no_real_local_server(settings):
    """Point the local model at a port nothing listens on, so a test that forgets
    to stub local.ask fails fast instead of asking the real ds4 server."""
    settings.ADVICE_LOCAL_URL = "http://127.0.0.1:9/v1"
