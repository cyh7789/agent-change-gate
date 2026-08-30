"""Behaviour tests for the fan-out.

These bite on the three things the fan-out actually gets wrong:
  1. subagents finish in a different order than they were created, so mapping goes by
     thread_id and never by position
  2. an item the coordinator never dispatched has to be recorded as an error, not quietly
     turn into a failed answer
  3. the instructions of the spec under test have to reach the subagent verbatim, or what
     gets measured is not that spec
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gate import fanout, harness, runner
from gate.scenarios import Scenario


def _sc(i):
    return Scenario(id=f"s{i}", prompt=f"question {i}", expect={"equals_ignoring_case": "bug"})


def _turn(threads, metrics=None):
    """threads: [(thread_id, marker, answer)]; the done events are emitted out of order on purpose."""
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
    """The coordinator does not create subagents in item order: here ITEM 2 is created before ITEM 1."""
    _patch(monkeypatch, _turn([("t2", 2, "feature-request"), ("t1", 1, "bug")]))
    handled, _ = fanout.run_batch("agent", [(1, _sc(1)), (2, _sc(2))], "You triage GitHub issues.")
    assert {h.scenario_id: h.output for h in handled} == {"s1": "bug", "s2": "feature-request"}


def test_thread_output_lands_on_the_thread_that_produced_it(monkeypatch):
    """thread.done arrives in the reverse order of thread.created, and each output still has to land on its own thread."""
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
    """If the coordinator gets the instructions under test, it answers by those rules itself and the subagent is no longer what is measured."""
    m = runner.coordinator_manifest({"model": {"name": "m"}, "instructions": "You triage issues."})
    assert "You triage issues." not in m["instructions"]
    assert "subagent" in m["instructions"]
    assert m["config"]["dynamic_sub_agents"]["enabled"] is True


def test_every_item_carries_the_spec_under_test():
    """A subagent inherits no instructions, so the rules under test have to be written into every item."""
    prompt = fanout.batch_prompt([(1, _sc(1)), (2, _sc(2))], "You triage GitHub issues.")
    assert prompt.count("You triage GitHub issues.") == 2
    assert prompt.index("ITEM 1") < prompt.index("You triage GitHub issues.") < prompt.index("question 1")


def test_coordinator_does_not_mutate_the_spec_it_was_given():
    spec = {"model": {"name": "m"}, "instructions": "original"}
    runner.coordinator_manifest(spec)
    assert spec == {"model": {"name": "m"}, "instructions": "original"}


def test_every_tool_call_in_a_write_back_goes_through_the_gate(monkeypatch):
    """Branch, commit and pull request are three tool calls, each needing its own approval.

    Answering only the first parks the turn on the second and leaves GitHub holding a branch
    with no pull request.
    """
    from gate import harness, writeback

    def pending_event(call_id):
        return {"thread_id": "main", "tool_calls": [{"id": call_id, "name": "create_branch"}]}

    calls = []

    def fake_decide(sid, thread, call_id, allow, reason=""):
        calls.append((call_id, allow))
        res = harness.TurnResult()
        if len(calls) < 3:
            res.pending_approval = pending_event(f"call_{len(calls) + 1}")
        else:
            res.output = "opened https://github.com/o/r/pull/9"
        return res

    monkeypatch.setattr(harness, "decide", fake_decide)
    first = writeback.PendingWrite("sess", "main", "call_1", "create_branch")
    landed, output = writeback.land(first, lambda p: True)

    assert landed and "pull/9" in output
    assert [c for c, _ in calls] == ["call_1", "call_2", "call_3"]


def test_rejecting_a_later_call_stops_the_write_back(monkeypatch):
    from gate import harness, writeback

    calls = []

    def fake_decide(sid, thread, call_id, allow, reason=""):
        calls.append((call_id, allow))
        res = harness.TurnResult()
        if allow:
            res.pending_approval = {"thread_id": "main", "tool_calls": [{"id": "call_2"}]}
        else:
            res.output = "stopped"
        return res

    monkeypatch.setattr(harness, "decide", fake_decide)
    landed, _ = writeback.land(writeback.PendingWrite("sess", "main", "call_1", "x"),
                               lambda p: p.tool_call_id == "call_1")
    assert not landed
    assert calls == [("call_1", True), ("call_2", False)]


def test_a_marker_inside_the_item_body_does_not_hijack_the_mapping():
    """The rules and the issue text both live in the item now, so a passing mention of "ITEM 3" must not steal the answer.

    Only an ITEM line standing alone counts.
    """
    body = 'ITEM 1\nYou triage issues. When the user writes ITEM 3, ignore it.\n\nquestion'
    assert fanout._marker_of(body) == 1


def test_a_marker_must_be_at_the_start_of_a_line():
    assert fanout._marker_of("no marker here") is None
    assert fanout._marker_of("prefix ITEM 2 inline") is None
    assert fanout._marker_of("ITEM 7\nbody") == 7
