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
                  "preferred": "Sonnet", "summary": "sum", "on_actual": "you were fine"}}
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
    assert snap["my_stack"] == 60300 - 650  # chips behind now, after my preflop call
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
    # every step recorded, in order, with timings and each model's move
    assert [(st["label"], st["status"]) for st in rec.steps] == [
        ("Qwen (local)", "done"), ("gpt-oss (local)", "done"),
        ("Sonnet's move", "done"), ("Sonnet judges", "done"),
    ]
    assert all(st["seconds"] is not None and st["started"] for st in rec.steps)
    assert rec.steps[0]["detail"]


def test_engine_sonnet_failure(my_turn, fakes, monkeypatch):
    def bad(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "boom")
    monkeypatch.setattr(sonnet.subprocess, "run", bad)
    rec = engine.run(Recommendation.objects.create(hand=my_turn, decision_key="k"))
    assert rec.status == "done"  # locals still produced advice
    assert rec.opinions[0]["error"].startswith("ClaudeError: claude exited 1")
    assert rec.judgement == {} and rec.concurrence == ""
    assert [st["status"] for st in rec.steps][2:] == ["error", "skipped"]


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
    monkeypatch.setattr(engine, "build_prompt", lambda h, p=None: 1 / 0)
    rec = engine.run(Recommendation.objects.create(hand=my_turn, decision_key="k"))
    assert rec.status == "error" and "ZeroDivisionError" in rec.error and rec.finished_at


# ---------- due decisions ----------
def test_due_decisions_idempotent(my_turn):
    first = engine.due_decisions()
    assert len(first) == 1
    r = first[0]
    assert r.decision_key == f"seq{my_turn.last_sequence}" and r.status == "pending" and not r.requested
    assert r.point == "r2n2" and (r.table_name, r.hand_number) == ("b17", 2)
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


# ---------- past points ----------
@pytest.fixture
def over(fakes):
    call_command("import_eml", str(FIXTURES))
    return Hand.objects.get(table__name="b17", number=2)


def test_engine_past_point(over, fakes):
    rec = Recommendation.objects.create(hand=over, point="r2n1", decision_key=engine.past_decision_key("r2n1"))
    assert rec.decision_key.startswith("pt-r2n1-") and len(rec.decision_key.split("-")[-1]) == 20
    engine.run(rec)
    rec.refresh_from_db()
    assert rec.status == "done", rec.error
    assert rec.actual == {"action": "fold", "amount": None, "text": "sefk folds"}
    assert rec.actual_grade == "different_action"  # Sonnet (fake) says call
    assert rec.judgement["on_actual"] == "you were fine"
    assert rec.snapshot["to_call"] == 600 and rec.snapshot["board"] == "As 6c Th"
    assert "4c" not in rec.prompt and "sefk folds" not in rec.prompt
    (c1, _), (c2, _) = fakes["run"]
    assert "sefk folds" in c2[-1] and "on_actual" in c2[-1] and "right and the models wrong" not in c2[-1]
    assert "may conclude the player was right" in c2[-1]
    for name in ("--json-schema", "--system-prompt", "--model", "--tools", "--setting-sources"):
        assert flag(c1, name) == flag(c2, name)
    schema = json.loads(flag(c1, "--json-schema"))
    j = schema["properties"]["judgement"]["anyOf"][0]
    assert "on_actual" in j["required"] and j["properties"]["on_actual"] == {"type": "string"}
    assert "on_actual" in flag(c1, "--system-prompt")


def test_engine_past_bet_actual_is_street_total(over, fakes):
    from poker.models import Action
    a = Action.objects.get(hand=over, text="sefk folds")
    a.kind, a.amount, a.text = "bet", 900, "sefk bets $900"
    a.save()
    rec = engine.run(Recommendation.objects.create(hand=over, point="r2n1", decision_key="pt-r2n1-x"))
    assert rec.actual == {"action": "bet", "amount": 900, "text": "sefk bets $900"}
    assert rec.actual_grade == "different_action"


def test_engine_live_point_has_no_actual(my_turn, fakes):
    rec = engine.run(Recommendation.objects.create(hand=my_turn, point="r2n2", decision_key="seq1"))
    assert rec.actual == {} and rec.actual_grade == ""
    assert "Set `on_actual` to an empty string" in fakes["run"][1][0][-1]
    assert "Amount to call: $400" in rec.prompt  # live path, not a replay


def test_engine_legacy_blank_point_is_live(my_turn, fakes):
    rec = engine.run(Recommendation.objects.create(hand=my_turn, decision_key="k"))
    assert rec.status == "done" and rec.actual == {}


def test_engine_past_point_unknown(over, fakes):
    rec = engine.run(Recommendation.objects.create(hand=over, point="r9n9", decision_key="pt-r9n9-x"))
    assert rec.status == "error" and "r9n9" in rec.error


def test_recommend_point_command(over, fakes, capsys):
    call_command("recommend", "b17", "2", "--point", "r1n1")
    out = capsys.readouterr().out
    assert "I played: sefk calls" in out and "actual_grade=agree" in out and "on my actual move: you were fine" in out
    rec = Recommendation.objects.get(point="r1n1")
    assert rec.decision_key.startswith("pt-r1n1-") and rec.requested and rec.status == "done"
    assert rec.table_name == "b17" and rec.hand_number == 2
    call_command("recommend", "b17", "2", "--point", "r1n1")  # a new row each time
    assert Recommendation.objects.filter(point="r1n1").count() == 2
    with pytest.raises(Exception, match="no decision"):
        call_command("recommend", "b17", "2", "--point", "r3n1")


def test_recommend_all_points(over, fakes, capsys):
    call_command("recommend", "b17", "2", "--all-points")
    assert sorted(Recommendation.objects.values_list("point", flat=True)) == ["r1n1", "r2n1"]
    n = len(fakes["run"])
    call_command("recommend", "b17", "2", "--all-points")
    assert "already done" in capsys.readouterr().out and len(fakes["run"]) == n
    call_command("recommend", "b17", "2", "--all-points", "--force")
    assert Recommendation.objects.count() == 4


def test_worker_processes_past_rows(over, fakes):
    Recommendation.objects.create(hand=over, point="r1n1", decision_key="pt-r1n1-1", requested=True)
    call_command("recommend_worker", "--once")
    rec = Recommendation.objects.get(point="r1n1")
    assert rec.status == "done" and rec.actual["action"] == "call"


def test_auth_error_is_explained_and_fails_the_recommendation():
    from poker.advice import sonnet

    msg = sonnet._explain("Failed to authenticate: OAuth session expired and could not be refreshed")
    assert sonnet.is_auth_error(msg)
    assert "claude setup-token" in msg
    assert not sonnet.is_auth_error(sonnet._explain("rate limited"))


def test_ensure_server_starts_lm_studio_when_down(monkeypatch):
    from poker.advice import local

    calls = []
    state = {"up": False}
    monkeypatch.setattr(local, "server_up", lambda: state["up"])

    def fake_run(cmd, **kw):
        calls.append(cmd)
        state["up"] = True

    monkeypatch.setattr("subprocess.run", fake_run)
    assert local.ensure_server() is True
    assert calls and calls[0][1:] == ["server", "start"]
    calls.clear()
    assert local.ensure_server() is True and calls == []  # already up: no-op
