import pytest

from poker.management.commands.recommend_worker import Command
from poker.models import Recommendation

pytestmark = pytest.mark.django_db


@pytest.fixture
def ran(monkeypatch):
    out = []
    monkeypatch.setattr("poker.advice.engine.due_decisions", lambda: [])

    def run(rec):
        out.append(rec.pk)
        rec.status = Recommendation.DONE
        rec.save()

    monkeypatch.setattr("poker.advice.engine.run", run)
    return out


def test_pause_file_holds_pending_work(settings, tmp_path, ran):
    settings.ADVICE_PAUSE_FILE = tmp_path / ".deploy-pause"
    rec = Recommendation.objects.create(decision_key="seq1")
    settings.ADVICE_PAUSE_FILE.touch()
    Command().pass_once()
    assert ran == []
    settings.ADVICE_PAUSE_FILE.unlink()
    Command().pass_once()
    assert ran == [rec.pk]
