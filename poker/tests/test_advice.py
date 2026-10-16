import json
import subprocess
from pathlib import Path

import pytest
from django.core.management import call_command

from poker.advice import engine, grading, local, sonnet
from poker.advice.prompt import build_prompt
from poker.models import Hand, Recommendation

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.django_db


# ---------- grading ----------
def op(action, amount=None, **kw):
    return {"action": action, "amount": amount, "error": "", **kw}


@pytest.mark.parametrize("a,b,to_call,stack,expected", [
    (op("fold"), op("fold"), 100, 1000, "agree"),
    (op("fold"), op("call"), 100, 1000, "different_action"),
    (op("check"), op("call"), 0, 1000, "agree"),
    (op("check"), op("call"), 100, 1000, "different_action"),
    (op("call"), op("call"), 100, 1000, "agree"),
    (op("bet", 500), op("bet", 550), 0, 1000, "agree"),
    (op("bet", 500), op("bet", 900), 0, 1000, "same_action_different_size"),
    (op("bet", 500), op("raise", 520), 100, 1000, "agree"),
    (op("raise", 500), op("call"), 100, 1000, "different_action"),
    (op("all_in"), op("raise", 1000), 100, 1000, "agree"),
    (op("all_in"), op("bet", 400), 0, 1000, "same_action_different_size"),
    (op("all_in", 1000), op("all_in"), 0, 1000, "agree"),
    (op("bet", 500), op("bet"), 0, 1000, "same_action_different_size"),
    (op("bet", error="x"), op("bet", 500), 0, 1000, ""),
])
def test_grade(a, b, to_call, stack, expected):
    assert grading.grade(a, b, to_call, stack) == expected


def test_overall():
    assert grading.overall(["agree", "agree"]) == "agree"
    assert grading.overall(["agree", "same_action_different_size"]) == "same_action_different_size"
    assert grading.overall(["same_action_different_size", "different_action"]) == "different_action"
    assert grading.overall(["", "agree"]) == "agree"
    assert grading.overall(["", ""]) == ""


# ---------- helpers ----------
@pytest.fixture
def my_turn():
    call_command("import_eml", str(FIXTURES))
    h = Hand.objects.get(table__name="b17", number=2)
    me = h.seats.get(player__name="sefk")
    h.is_over = False
    h.next_to_act = me.player
    h.to_call = 400
    h.betting_round = 2
    h.save()
    me.status = ""
    me.save()
    return h


@pytest.fixture
def fakes(monkeypatch, tmp_path, settings):
    settings.ADVICE_CLAUDE_CWD = tmp_path / "cwd"
    calls = {"post": [], "run": []}

    def fake_post(url, payload, timeout):
        calls["post"].append((url, payload))
        body = {"action": "call", "amount": None, "command": "call *", "reasoning": "pot odds"}
        if payload["model"].startswith("openai"):
            body = {"action": "raise", "amount": 1200, "command": "BET $1200", "reasoning": "value"}
        return {"choices": [{"message": {"content": json.dumps(body)}}]}

    def fake_run(cmd, **kw):
        calls["run"].append((cmd, kw))
        resume = "--resume" in cmd
        so = ({"recommendation": None, "judgement": {
                  "verdicts": [{"model": "Qwen", "concurrence": "agree", "evaluation": "ok"},
                               {"model": "gpt-oss", "concurrence": "different_action", "evaluation": "no"}],
                  "preferred": "Sonnet", "summary": "sum"}}
              if resume else
              {"recommendation": {"action": "call", "amount": None, "command": "call *", "reasoning": "r"},
               "judgement": None})
        out = {"session_id": "sess-1", "total_cost_usd": 0.05, "is_error": False, "structured_output": so,
               "usage": {"cache_read_input_tokens": 1234 if resume else 0}}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    monkeypatch.setattr(local, "_post", fake_post)
    monkeypatch.setattr(sonnet.subprocess, "run", fake_run)
    return calls


def flag(cmd, name):
    return cmd[cmd.index(name) + 1]


# ---------- prompt ----------
def test_prompt_content(my_turn):
    text, snap = build_prompt(my_turn)
    assert snap["cards"] == "9d 9s"
    assert snap["board"] == "As 6c Th 4c 2h"
    assert snap["betting_round"] == "flop"
    assert snap["next_to_act"] == "sefk"
    assert snap["to_call"] == 400
    assert snap["my_stack"] == my_turn.seats.get(player__name="sefk").bankroll_start
    for needle in ("Table b17, hand 2", "dealer button", "YOU", "9d 9s", "Amount to call: $400",
                   "Capparella", "Flop:", "TOTAL amount", "slow email tournament"):
        assert needle in text
    assert "Your position:" in text


# ---------- local + sonnet plumbing ----------
def test_local_error_is_recorded(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(local, "_post", boom)
    o = local.ask("m", "M", "p")
    assert "connection refused" in o["error"] and o["action"] == ""


def test_local_uses_json_schema(fakes):
    local.ask("qwen/x", "Qwen", "p")
    url, payload = fakes["post"][0]
    assert url.endswith("/chat/completions")
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True


def test_local_strips_think():
    assert local._parse('<think>hmm {x}</think>{"a": 1}') == {"a": 1}


# ---------- engine ----------
def test_engine_full_run(my_turn, fakes):
    rec = Recommendation.objects.create(hand=my_turn, decision_key=engine.decision_key(my_turn))
    engine.run(rec)
    rec.refresh_from_db()
    assert rec.status == "done", rec.error
    assert [o["label"] for o in rec.opinions] == ["Sonnet", "Qwen", "gpt-oss"]
    assert [o["grade"] for o in rec.opinions] == ["", "agree", "different_action"]
    assert rec.concurrence == "different_action"
    assert float(rec.cost_usd) == pytest.approx(0.10)
    assert rec.judgement["preferred"] == "Sonnet"
    assert rec.judgement["cache_read_tokens"] == 1234
    assert rec.started_at and rec.finished_at and rec.prompt and rec.snapshot["to_call"] == 400
    # locals ran sequentially in configured order
    assert [p["model"] for _, p in fakes["post"]] == ["qwen/qwen3.6-35b-a3b", "openai/gpt-oss-20b"]

    (c1, kw1), (c2, _) = fakes["run"]
    for name in ("--json-schema", "--system-prompt", "--model", "--tools", "--setting-sources"):
        assert flag(c1, name) == flag(c2, name)
    assert "--strict-mcp-config" in c1 and "--strict-mcp-config" in c2
    assert "--resume" not in c1 and flag(c2, "--resume") == "sess-1"
    assert Path(kw1["cwd"]).is_dir()
    assert "Qwen answered" in c2[-1] and "gpt-oss answered" in c2[-1] and "side with a local model" in c2[-1]
    assert json.loads(flag(c1, "--json-schema"))["required"] == ["recommendation", "judgement"]


def test_engine_sonnet_failure(my_turn, fakes, monkeypatch):
    def bad(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "boom")
    monkeypatch.setattr(sonnet.subprocess, "run", bad)
    rec = engine.run(Recommendation.objects.create(hand=my_turn, decision_key="k"))
    assert rec.status == "done"  # locals still produced advice
    assert rec.opinions[0]["error"].startswith("ClaudeError: claude exited 1")
    assert rec.judgement == {} and rec.concurrence == ""


def test_engine_timeout_and_is_error(my_turn, fakes, monkeypatch):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(sonnet.subprocess, "run", slow)
    o, sid, cost = sonnet.recommend("p")
    assert "timed out" in o["error"] and sid is None

    def iserr(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"is_error": True, "result": "bad"}), "")
    monkeypatch.setattr(sonnet.subprocess, "run", iserr)
    assert "claude error" in sonnet.recommend("p")[0]["error"]


def test_engine_all_fail(my_turn, monkeypatch, settings, tmp_path):
    settings.ADVICE_CLAUDE_CWD = tmp_path
    monkeypatch.setattr(local, "_post", lambda *a: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(sonnet.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, "", "x"))
    rec = engine.run(Recommendation.objects.create(hand=my_turn, decision_key="k"))
    assert rec.status == "error" and "down" in rec.error


def test_engine_unexpected_exception(my_turn, monkeypatch):
    monkeypatch.setattr(engine, "build_prompt", lambda h: 1 / 0)
    rec = engine.run(Recommendation.objects.create(hand=my_turn, decision_key="k"))
    assert rec.status == "error" and "ZeroDivisionError" in rec.error and rec.finished_at


# ---------- due decisions ----------
def test_due_decisions_idempotent(my_turn):
    first = engine.due_decisions()
    assert len(first) == 1
    r = first[0]
    assert r.decision_key == f"seq{my_turn.last_sequence}" and r.status == "pending" and not r.requested
    assert engine.due_decisions() == []
    assert Recommendation.objects.count() == 1
    # a new broadcast is a new decision
    my_turn.last_sequence += 1
    my_turn.save()
    assert len(engine.due_decisions()) == 1


def test_due_decisions_skips(my_turn):
    me = my_turn.seats.get(player__name="sefk")
    me.status = "folded"
    me.save()
    assert engine.due_decisions() == []
    me.status = ""
    me.save()
    my_turn.is_over = True
    my_turn.save()
    assert engine.due_decisions() == []
    my_turn.is_over = False
    my_turn.next_to_act = my_turn.seats.exclude(player__name="sefk").first().player
    my_turn.save()
    assert engine.due_decisions() == []


def test_due_skips_when_requested_row_exists(my_turn):
    n = my_turn.last_sequence
    Recommendation.objects.create(hand=my_turn, decision_key=f"req-{n}-20261015T1200", requested=True)
    assert engine.due_decisions() == []


def test_local_reasoning_content_fallback(monkeypatch):
    monkeypatch.setattr(local, "_post", lambda *a: {"choices": [{"message": {
        "content": "", "reasoning_content": '{"action":"fold","amount":null,"command":"$0","reasoning":"r"}'}}]})
    assert local.ask("m", "M", "p")["action"] == "fold"


def test_reset_stale(my_turn):
    from datetime import timedelta
    from django.utils import timezone
    old = Recommendation.objects.create(hand=my_turn, decision_key="a", status="running",
                                        started_at=timezone.now() - timedelta(hours=1))
    new = Recommendation.objects.create(hand=my_turn, decision_key="b", status="running",
                                        started_at=timezone.now())
    assert engine.reset_stale() == 1
    old.refresh_from_db(); new.refresh_from_db()
    assert old.status == "pending" and new.status == "running"


# ---------- commands ----------
def test_recommend_command_and_worker(my_turn, fakes, capsys):
    call_command("recommend", "b17", "2")
    out = capsys.readouterr().out
    assert "Sonnet" in out and "preferred=Sonnet" in out
    n = len(fakes["run"])
    call_command("recommend", "b17", "2")  # already done
    assert len(fakes["run"]) == n
    call_command("recommend", "b17", "2", "--force")
    assert len(fakes["run"]) == 2 * n
    Recommendation.objects.all().delete()
    call_command("recommend_worker", "--once")
    rec = Recommendation.objects.get()
    assert rec.status == "done" and not rec.requested
