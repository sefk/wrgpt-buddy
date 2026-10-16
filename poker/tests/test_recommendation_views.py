from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from poker.models import Hand, Recommendation

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.django_db

SONNET = {"model": "claude-sonnet-4-6", "label": "Sonnet", "action": "raise", "amount": 600,
          "reasoning": "SONNET-REASON top pair, bet for value.", "error": "", "seconds": 12.5,
          "grade": "", "command": "MAKE $600"}
QWEN = {"model": "qwen/qwen3.6-35b-a3b", "label": "Qwen", "action": "raise", "amount": 600,
        "reasoning": "QWEN-REASON same line.", "error": "", "seconds": 41.2, "grade": "agree"}
GPT = {"model": "openai/gpt-oss-20b", "label": "gpt-oss", "action": "fold", "amount": None,
       "reasoning": "GPT-REASON too thin.", "error": "", "seconds": 20.0, "grade": "different_action"}


@pytest.fixture
def hand(client):
    call_command("import_eml", str(FIXTURES))
    return Hand.objects.get(table__name="b17", number=2)


def url(hand):
    return reverse("poker:hand", args=[hand.table.name, hand.number])


def make(hand, **kw):
    kw.setdefault("decision_key", f"seq{hand.last_sequence}")
    kw.setdefault("status", Recommendation.DONE)
    return Recommendation.objects.create(hand=hand, **kw)


def done(hand, agree=True, revealed=True, **kw):
    gpt = dict(GPT, grade="agree", action="raise", amount=600, reasoning="GPT-REASON same.") if agree else GPT
    judgement = {"verdicts": [], "preferred": "Sonnet", "summary": "AGREE-SUMMARY"}
    if not agree:
        judgement = {"verdicts": [{"model": "gpt-oss", "concurrence": "different action",
                                   "evaluation": "JUDGE-EVAL-GPT folding is too tight."}],
                     "preferred": "Sonnet", "summary": "DISAGREE-SUMMARY", "cache_read_tokens": 1234}
    return make(
        hand, opinions=[SONNET, QWEN, gpt], judgement=judgement,
        concurrence="agree" if agree else "different_action",
        snapshot={"board": hand.board, "pot": hand.pot}, cost_usd="0.0123",
        revealed_at=timezone.now() if revealed else None, **kw)


def test_no_recommendation_offers_request(hand, client):
    r = client.get(url(hand))
    assert b"Request analysis" in r.content


def test_request_creates_pending(hand, client):
    r = client.post(reverse("poker:request_analysis", args=["b17", 2]))
    assert r.status_code == 302
    rec = hand.recommendations.get()
    assert rec.status == "pending" and rec.requested
    assert rec.decision_key.startswith(f"req-{hand.last_sequence}-")
    client.post(reverse("poker:request_analysis", args=["b17", 2]))
    assert hand.recommendations.count() == 2


def test_post_required(hand, client):
    assert client.get(reverse("poker:request_analysis", args=["b17", 2])).status_code == 405
    rec = done(hand, revealed=False)
    assert client.get(reverse("poker:reveal", args=["b17", 2, rec.pk])).status_code == 405


@pytest.mark.parametrize("status", ["pending", "running"])
def test_in_progress_shows_analyzing_and_refreshes(hand, client, status):
    make(hand, status=status, started_at=timezone.now() - timedelta(minutes=3))
    hand.is_over = True
    hand.save()
    r = client.get(url(hand))
    assert b"Analyzing" in r.content
    assert b"3 min ago" in r.content
    assert b'http-equiv="refresh"' in r.content


def test_no_refresh_when_idle_and_over(hand, client):
    hand.is_over = True
    hand.save()
    assert b'http-equiv="refresh"' not in client.get(url(hand)).content


def test_error_collapsed_with_retry(hand, client):
    make(hand, status="error", error="LM Studio unreachable")
    r = client.get(url(hand))
    assert b"LM Studio unreachable" in r.content
    assert b"<details>" in r.content
    assert b"Retry" in r.content


def test_done_hidden_until_revealed(hand, client):
    rec = done(hand, revealed=False)
    body = client.get(url(hand)).content
    assert b"Ready" in body and b"3 opinions" in body
    assert b"Show recommendation" in body
    for hidden in (b"SONNET-REASON", b"QWEN-REASON", b"GPT-REASON", b"MAKE $600", b"AGREE-SUMMARY"):
        assert hidden not in body
    r = client.post(reverse("poker:reveal", args=["b17", 2, rec.pk]))
    assert r.status_code == 302
    rec.refresh_from_db()
    assert rec.revealed_at is not None
    body = client.get(url(hand)).content
    assert b"SONNET-REASON" in body and b"QWEN-REASON" in body and b"GPT-REASON" in body
    assert b"MAKE $600" in body
    assert b"Show recommendation" not in body


def test_reveal_keeps_first_timestamp_and_ignores_unfinished(hand, client):
    rec = done(hand, revealed=True)
    first = rec.revealed_at
    client.post(reverse("poker:reveal", args=["b17", 2, rec.pk]))
    rec.refresh_from_db()
    assert rec.revealed_at == first
    pending = make(hand, decision_key="other", status="pending")
    client.post(reverse("poker:reveal", args=["b17", 2, pending.pk]))
    pending.refresh_from_db()
    assert pending.revealed_at is None


def test_agree_header_no_callout(hand, client):
    done(hand, agree=True)
    body = client.get(url(hand)).content.decode()
    assert "All three agree" in body
    assert "raise" in body and "$600" in body
    assert "callout differ" not in body
    assert "JUDGE-EVAL" not in body


def test_disagreement_callout_and_verdict(hand, client):
    done(hand, agree=False)
    body = client.get(url(hand)).content.decode()
    assert "callout differ" in body
    assert "DISAGREE-SUMMARY" in body
    assert "judge prefers Sonnet" in body
    assert "JUDGE-EVAL-GPT" in body
    assert "All three agree" not in body
    assert "different action" in body
    assert "cost $0.0123" in body and "1234 cache-read" in body
    # evaluation sits right after gpt-oss' card, before nothing else follows it
    assert body.index("GPT-REASON") < body.index("JUDGE-EVAL-GPT")


def test_stale_detection(hand, client):
    rec = done(hand)
    assert "Stale" not in client.get(url(hand)).content.decode()
    hand.last_sequence += 1
    hand.save()
    body = client.get(url(hand)).content.decode()
    assert "computed for an earlier state" in body and "Re-analyze" in body
    # snapshot drift without a sequence change
    hand.last_sequence -= 1
    hand.save()
    rec.snapshot = {"board": "2c 3c 4c", "pot": 1}
    rec.save()
    assert "computed for an earlier state" in client.get(url(hand)).content.decode()


def test_older_recommendations_listed(hand, client):
    old = done(hand, decision_key="seq1")
    Recommendation.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=2))
    done(hand, decision_key="seq2", agree=False)
    body = client.get(url(hand)).content.decode()
    assert "Older recommendations (1)" in body


def test_history_indicator_only_when_revealed(hand, client):
    assert "rec-agree" not in client.get("/hands/").content.decode()
    rec = done(hand, agree=True, revealed=False)
    body = client.get("/hands/").content.decode()
    assert "rec-agree" not in body and "rec-differ" not in body
    Recommendation.objects.filter(pk=rec.pk).update(revealed_at=timezone.now())
    assert 'class="rec-agree"' in client.get("/hands/").content.decode()
    Recommendation.objects.filter(pk=rec.pk).update(concurrence="different_action")
    assert 'class="rec-differ"' in client.get("/hands/").content.decode()
