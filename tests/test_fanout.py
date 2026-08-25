"""扇出的行為測試。

咬的是扇出真的會錯的三件事：
  1. subagent 完成順序跟建立順序不同，對映必須走 thread_id 而不是順序
  2. 協調者漏發某一題時，那題要記成錯誤，不能靜悄悄變成「沒通過」
  3. 受測 spec 的 instructions 要原封不動傳給 subagent，否則量到的不是它
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gate import fanout, harness, runner
from gate.scenarios import Scenario


def _sc(i):
    return Scenario(id=f"s{i}", prompt=f"question {i}", expect={"equals_ignoring_case": "bug"})


def _turn(threads, metrics=None):
    """threads: [(thread_id, marker, answer)]，done 事件刻意亂序送出。"""
    events = [harness.Event(0, "turn.created", {})]
    for tid, marker, _ in threads:
        events.append(harness.Event(None, "thread.created", {
            "thread_id": tid,
            "agent_info": {"type": "dynamic", "name": tid,
                           "input": f"ITEM {marker}\nquestion"}}))
    for tid, _, answer in reversed(threads):
        events.append(harness.Event(None, "thread.done", {
            "thread_id": tid,
            "state": {"status": "done", "output": {"content": answer}}}))
    events.append(harness.Event(9, "turn.done", {
        "state": {"output": {"content": "{}"}, "metrics": metrics or {}}}))
    res = harness.TurnResult(events=events)
    res.output = "{}"
    res.metrics = metrics or {}
    return res


def _patch(monkeypatch, res):
    monkeypatch.setattr(harness, "create_session", lambda name: "sess")
    monkeypatch.setattr(harness, "run_turn", lambda sid, msg, stop_at_approval=True: res)


def test_answers_follow_the_item_marker_not_the_spawn_order(monkeypatch):
    """協調者不保證照題目順序開 subagent：這裡先開 ITEM 2 再開 ITEM 1。"""
    _patch(monkeypatch, _turn([("t2", 2, "feature-request"), ("t1", 1, "bug")]))
    handled, _ = fanout.run_batch("agent", [(1, _sc(1)), (2, _sc(2))], "You triage GitHub issues.")
    assert {h.scenario_id: h.output for h in handled} == {"s1": "bug", "s2": "feature-request"}


def test_thread_output_lands_on_the_thread_that_produced_it(monkeypatch):
    """thread.done 的到達順序跟 thread.created 相反，輸出仍要掛回自己的 thread。"""
    res = _turn([("t1", 1, "bug"), ("t2", 2, "feature-request")])
    threads = {t.thread_id: t.output for t in res.threads}
    assert threads == {"t1": "bug", "t2": "feature-request"}


def test_repeated_scenario_in_one_batch_keeps_both_answers(monkeypatch):
    _patch(monkeypatch, _turn([("t2", 2, "feature-request"), ("t1", 1, "bug")]))
    handled, _ = fanout.run_batch("agent", [(1, _sc(1)), (2, _sc(1))], "You triage GitHub issues.")
    assert [h.output for h in handled] == ["bug", "feature-request"]


def test_item_no_subagent_took_is_an_error_not_a_failed_answer(monkeypatch):
    _patch(monkeypatch, _turn([("t1", 1, "bug")]))
    handled, _ = fanout.run_batch("agent", [(1, _sc(1)), (2, _sc(2))], "You triage GitHub issues.")
    missed = [h for h in handled if h.scenario_id == "s2"][0]
    assert missed.output is None
    assert "no subagent" in missed.error


def test_errored_thread_does_not_count_as_an_answer(monkeypatch):
    res = _turn([("t1", 1, "bug")])
    for e in res.events:
        if e.type == "thread.done":
            e.data["state"] = {"status": "error", "message": "boom"}
    _patch(monkeypatch, res)
    handled, _ = fanout.run_batch("agent", [(1, _sc(1))], "You triage GitHub issues.")
    assert handled[0].output is None and handled[0].error


def test_batch_metrics_come_from_the_turn(monkeypatch):
    _patch(monkeypatch, _turn([("t1", 1, "bug")], {"total_tokens": 4242}))
    _, metrics = fanout.run_batch("agent", [(1, _sc(1))], "You triage GitHub issues.")
    assert metrics["total_tokens"] == 4242


def test_the_coordinator_does_not_carry_the_spec_under_test():
    """協調者拿到受測 instructions 的話，它自己就會照那份規則作答，測的就不是 subagent 了。"""
    m = runner.coordinator_manifest({"model": {"name": "m"}, "instructions": "You triage issues."})
    assert "You triage issues." not in m["instructions"]
    assert "subagent" in m["instructions"]
    assert m["config"]["dynamic_sub_agents"]["enabled"] is True


def test_every_item_carries_the_spec_under_test():
    """subagent 繼承不到 instructions，所以受測規則必須寫在每個 item 裡。"""
    prompt = fanout.batch_prompt([(1, _sc(1)), (2, _sc(2))], "You triage GitHub issues.")
    assert prompt.count("You triage GitHub issues.") == 2
    assert prompt.index("ITEM 1") < prompt.index("You triage GitHub issues.") < prompt.index("question 1")


def test_coordinator_does_not_mutate_the_spec_it_was_given():
    spec = {"model": {"name": "m"}, "instructions": "original"}
    runner.coordinator_manifest(spec)
    assert spec == {"model": {"name": "m"}, "instructions": "original"}
