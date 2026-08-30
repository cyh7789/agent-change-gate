"""Three things about the sandbox statistical reading.

This section is an add-on: getting it wrong or failing to run must not touch the verdict, and
must not take the gate down with it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

from gate import analysis, harness

ROWS = [{"id": "a", "baseline_pass": 3, "baseline_n": 3, "candidate_pass": 1, "candidate_n": 2},
        {"id": "b", "baseline_pass": 0, "baseline_n": 0, "candidate_pass": 3, "candidate_n": 3}]


def test_each_arm_carries_its_own_number_of_runs():
    """The arms can have different counts: with failed runs in one arm, a shared n makes the sandbox compute the wrong rate."""
    sent = json.loads(analysis.payload(ROWS))
    assert sent[0] == {"id": "a", "baseline": 3, "baseline_n": 3, "candidate": 1, "candidate_n": 2}
    assert sent[1]["baseline_n"] == 0 and sent[1]["candidate_n"] == 3


def test_a_broken_analyst_does_not_take_the_gate_down(monkeypatch):
    """The evaluation ran for minutes and must not be thrown away because an add-on reading broke."""
    def boom(*a, **k):
        raise ValueError("provider returned something unparseable")

    monkeypatch.setattr(harness, "create_agent", boom)
    assert analysis.interpret(ROWS) is None


def test_an_analyst_that_only_talked_is_not_accepted(monkeypatch):
    """No tool response means the code never ran; numbers the model narrated do not count as executed."""
    res = harness.TurnResult(events=[harness.Event(1, "sandbox.created", {"sandbox_id": "s"}),
                                     harness.Event(2, "turn.done", {})])
    res.output = "The pass rates are roughly equal."
    monkeypatch.setattr(harness, "create_agent", lambda *a, **k: "id")
    monkeypatch.setattr(harness, "create_session", lambda *a, **k: "sid")
    monkeypatch.setattr(harness, "run_turn", lambda *a, **k: res)
    assert analysis.interpret(ROWS) is None


def test_an_analyst_that_ran_code_is_accepted(monkeypatch):
    res = harness.TurnResult(events=[harness.Event(1, "sandbox.created", {"sandbox_id": "s"}),
                                     harness.Event(2, "tool.response", {"content": "exitCode 0"}),
                                     harness.Event(3, "turn.done", {})])
    res.output = "  #### Read\n\n| arm | rate |\n"
    monkeypatch.setattr(harness, "create_agent", lambda *a, **k: "id")
    monkeypatch.setattr(harness, "create_session", lambda *a, **k: "sid")
    monkeypatch.setattr(harness, "run_turn", lambda *a, **k: res)
    assert analysis.interpret(ROWS).startswith("#### Read")
