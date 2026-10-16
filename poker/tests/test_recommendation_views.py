from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from poker.models import Hand, Player, Recommendation

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
    kw.setdefault("point", "")
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


def go_live(hand):
    """Make it my turn on the flop: live point r2n2 (hand 2 has decisions r1n1, r2n1)."""
    hand.is_over = False
    hand.betting_round = 2
    hand.next_to_act = Player.objects.get(name="sefk")
    hand.save()
    return "r2n2"


def past(hand, point="r1n1", agree=False, **kw):
    rec = done(hand, agree=agree, decision_key=f"pt-{point}-x", point=point,
               actual={"action": "call", "amount": 400, "text": "sefk calls $400"},
               actual_grade="same_action_different_size", **kw)
    rec.judgement = dict(rec.judgement, on_actual="ON-ACTUAL-COMMENT you called too small.")
    rec.save()
    return rec


def body_of(client, hand):
    return client.get(url(hand)).content.decode()


def row_html(body, point):
    """The markup of one past-point control."""
    i = body.index(f'id="pt-{point}"')
    return body[i:i + 6000]


# --- controls per state --------------------------------------------------------------

def test_ask_button_on_each_of_my_decision_rows(hand, client):
    body = body_of(client, hand)
    assert body.count("Ask models") == 2
    assert 'id="pt-r1n1"' in body and 'id="pt-r2n1"' in body
    assert 'name="point" value="r1n1"' in body and 'name="point" value="r2n1"' in body
    assert "csrfmiddlewaretoken" in body
    assert "Recommendation</h2>" not in body


@pytest.mark.parametrize("status", ["pending", "running"])
def test_in_progress_inline_and_refreshes(hand, client, status):
    make(hand, point="r1n1", status=status, started_at=timezone.now() - timedelta(minutes=3))
    body = body_of(client, hand)
    assert "Analyzing" in row_html(body, "r1n1")[:600] and "3 min" in body
    assert body.count("Ask models") == 1  # r2n1 still offers it
    assert 'http-equiv="refresh"' in body


def test_no_refresh_when_idle_and_over(hand, client):
    past(hand)
    assert 'http-equiv="refresh"' not in body_of(client, hand)


def test_error_inline_with_retry(hand, client):
    make(hand, point="r2n1", status="error", error="LM Studio unreachable")
    body = body_of(client, hand)
    seg = row_html(body, "r2n1")
    assert "LM Studio unreachable" in seg and "Retry" in seg and "<details>" in seg
    assert 'value="r2n1"' in seg


def test_done_past_collapsed_summary_and_expanded_content(hand, client):
    past(hand)
    body = body_of(client, hand)
    seg = row_html(body, "r1n1")
    assert '<details class="models">' in seg  # collapsed by default
    summary = seg[seg.index("<summary>"):seg.index("</summary>")]
    assert "Sonnet raise $600" in summary and "Qwen raise $600" in summary and "gpt-oss fold" in summary
    assert "you: call $400" in summary and "different size" in summary
    # all content is in the page (details just hides it), no reveal needed
    for text in ("SONNET-REASON", "QWEN-REASON", "GPT-REASON", "ON-ACTUAL-COMMENT", "Judge on your move",
                 "sefk calls $400", "JUDGE-EVAL-GPT", "cost $0.0123", "1234 cache-read", "Re-run"):
        assert text in seg, text
    assert "Show recommendation" not in body
    assert "<details class=\"models\" open>" not in body
    assert "<details class=\"models\" open>" in client.get(url(hand) + "?open=r1n1").content.decode()


def test_latest_per_point_and_earlier_list(hand, client):
    old = past(hand)
    Recommendation.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=2))
    new = done(hand, agree=True, decision_key="pt-r1n1-y", point="r1n1")
    seg = row_html(body_of(client, hand), "r1n1")
    assert "Earlier analyses (1)" in seg
    assert "All three agree" in seg and "ON-ACTUAL" not in seg
    assert new.pk


# --- requests ---------------------------------------------------------------------------

def test_past_request_creates_pt_row(hand, client):
    r = client.post(reverse("poker:request_analysis", args=["b17", 2]), {"point": "r1n1"})
    assert r.status_code == 302 and r["Location"].endswith("#pt-r1n1")
    rec = hand.recommendations.get()
    assert (rec.point, rec.status, rec.requested) == ("r1n1", "pending", True)
    assert rec.decision_key.startswith("pt-r1n1-") and rec.hand == hand
    client.post(reverse("poker:request_analysis", args=["b17", 2]), {"point": "r1n1"})
    assert hand.recommendations.count() == 2


def test_live_request_sets_point_and_req_key(hand, client):
    live = go_live(hand)
    r = client.post(reverse("poker:request_analysis", args=["b17", 2]), {"point": live})
    assert r["Location"].endswith("#pt-live")
    rec = hand.recommendations.get()
    assert rec.point == live and rec.decision_key.startswith(f"req-{hand.last_sequence}-")
    client.post(reverse("poker:request_analysis", args=["b17", 2]))  # no point = live
    assert hand.recommendations.filter(point=live).count() == 2


def test_bogus_point_400_and_unknown_hand_404(hand, client):
    assert client.post(reverse("poker:request_analysis", args=["b17", 2]), {"point": "r9n9"}).status_code == 400
    assert client.post(reverse("poker:request_analysis", args=["b17", 2]), {"point": "r2n2"}).status_code == 400  # not live
    assert client.post(reverse("poker:request_analysis", args=["b17", 999]), {"point": "r1n1"}).status_code == 404
    assert hand.recommendations.count() == 0


def test_post_required(hand, client):
    assert client.get(reverse("poker:request_analysis", args=["b17", 2])).status_code == 405
    rec = done(hand, revealed=False)
    assert client.get(reverse("poker:reveal", args=["b17", 2, rec.pk])).status_code == 405


# --- live row -----------------------------------------------------------------------------

def test_live_row_absent_when_not_my_turn(hand, client):
    body = body_of(client, hand)
    assert 'id="pt-live"' not in body and "Ready &darr;" not in body


def test_live_row_ask_and_jump_link(hand, client):
    go_live(hand)
    body = body_of(client, hand)
    assert 'id="pt-live"' in body and "sefk to act" in body and "Flop" in body
    assert 'href="#pt-live">Ask models' in body
    make(hand, point="r2n2", status="pending")
    assert 'href="#pt-live">Analyzing' in body_of(client, hand)
    make(hand, point="r2n2", status="error", error="boom", decision_key="e2")
    assert 'href="#pt-live">Analysis failed' in body_of(client, hand)


def test_live_done_hidden_until_revealed(hand, client):
    live = go_live(hand)
    rec = done(hand, revealed=False, point=live)
    body = body_of(client, hand)
    assert "Ready" in body and "3 opinions" in body and "Show recommendation" in body
    assert 'href="#pt-live">Recommendation ready' in body
    for hidden in ("SONNET-REASON", "QWEN-REASON", "GPT-REASON", "MAKE $600", "AGREE-SUMMARY"):
        assert hidden not in body
    r = client.post(reverse("poker:reveal", args=["b17", 2, rec.pk]))
    assert r.status_code == 302 and r["Location"].endswith("#pt-live")
    rec.refresh_from_db()
    assert rec.revealed_at is not None
    body = body_of(client, hand)
    assert "SONNET-REASON" in body and "MAKE $600" in body and "Show recommendation" not in body


def test_legacy_live_row_matched_by_key(hand, client):
    go_live(hand)
    done(hand, revealed=True, decision_key=f"seq{hand.last_sequence}")
    done(hand, revealed=True, decision_key=f"req-{hand.last_sequence}-9")
    body = body_of(client, hand)
    live_seg = body[body.index('id="pt-live"'):]
    assert "SONNET-REASON" in live_seg and "Earlier analyses (1)" in live_seg
    assert "Other analyses" not in body


def test_live_stale_detection(hand, client):
    live = go_live(hand)
    rec = done(hand, point=live)
    assert "Stale" not in body_of(client, hand)
    hand.last_sequence += 1
    hand.save()
    body = body_of(client, hand)
    assert "computed for an earlier state" in body and "Re-analyze" in body
    hand.last_sequence -= 1
    hand.save()
    rec.snapshot = {"board": "2c 3c 4c", "pot": 1}
    rec.save()
    assert "computed for an earlier state" in body_of(client, hand)


def test_disagreement_callout_in_live_row(hand, client):
    live = go_live(hand)
    done(hand, agree=False, point=live)
    body = body_of(client, hand)
    assert "callout differ" in body and "judge prefers Sonnet" in body and "DISAGREE-SUMMARY" in body
    assert body.index("GPT-REASON") < body.index("JUDGE-EVAL-GPT")


# --- other analyses -------------------------------------------------------------------------

def test_unmatched_legacy_rows_under_other_analyses(hand, client):
    done(hand, decision_key="seq1")
    done(hand, decision_key="odd", point="r7n7")
    body = body_of(client, hand)
    assert "Other analyses (2)" in body
    assert body.index("Other analyses") > body.index('id="pt-r2n1"')
    assert "SONNET-REASON" in body


# --- history ----------------------------------------------------------------------------------

def test_history_indicator(hand, client):
    assert "rec-agree" not in client.get("/hands/").content.decode()
    rec = done(hand, agree=True, revealed=False)
    body = client.get("/hands/").content.decode()
    assert "rec-agree" not in body and "rec-differ" not in body
    Recommendation.objects.filter(pk=rec.pk).update(revealed_at=timezone.now())
    assert 'class="rec-agree"' in client.get("/hands/").content.decode()
    Recommendation.objects.filter(pk=rec.pk).update(concurrence="different_action")
    assert 'class="rec-differ"' in client.get("/hands/").content.decode()


def test_history_counts_past_points_as_revealed(hand, client):
    past(hand)  # revealed_at set by done(); clear it: past points count anyway
    Recommendation.objects.update(revealed_at=None)
    assert 'class="rec-differ"' in client.get("/hands/").content.decode()
