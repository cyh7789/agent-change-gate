"""Behaviour tests for the comparison.

These bite on the reason the report exists: separating noise from a real regression. The same
spec over the same scenarios three times running gave 15/16, 14/16, 14/16, with issue-332082
answering pass/fail/fail. A single run reports that scenario as a regression caused by the
change.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gate.report import Comparison
from gate.runner import ArmResult, ScenarioRun
from gate.scenarios import Scenario, ScenarioSet


def _set(*ids):
    return ScenarioSet(
        scenarios=[Scenario(id=i, prompt="q", expect={"equals_ignoring_case": "bug"}) for i in ids],
        source="test", revision="test", digest="d" * 64)


def _arm(label, answers):
    """answers: {scenario_id: [output of each run]}"""
    arm = ArmResult(label=label, agent_name=label)
    for sid, outs in answers.items():
        for o in outs:
            arm.runs.append(ScenarioRun(sid, o))
    arm.batches.append({"total_tokens": 100})
    return arm


def _delta(scenarios, base, cand, sid):
    rows = Comparison(scenarios, base, cand).rows()
    return next(r for r in rows if r["id"] == sid)["delta"]


def test_a_scenario_unstable_in_one_arm_is_flaky_not_broken():
    s = _set("a")
    base = _arm("baseline", {"a": ["bug", "bug", "bug"]})
    cand = _arm("candidate", {"a": ["bug", "feature-request", "bug"]})
    assert _delta(s, base, cand, "a") == "flaky"


def test_a_scenario_unstable_in_the_baseline_is_also_flaky():
    s = _set("a")
    base = _arm("baseline", {"a": ["bug", "feature-request", "bug"]})
    cand = _arm("candidate", {"a": ["bug", "bug", "bug"]})
    assert _delta(s, base, cand, "a") == "flaky"


def test_consistently_lost_is_broken():
    # Five repeats: a three-repeat difference never clears the test, see test_significance.py
    s = _set("a")
    base = _arm("baseline", {"a": ["bug"] * 5})
    cand = _arm("candidate", {"a": ["feature-request"] * 5})
    assert _delta(s, base, cand, "a") == "broken"


def test_consistently_gained_is_fixed():
    s = _set("a")
    base = _arm("baseline", {"a": ["feature-request"] * 5})
    cand = _arm("candidate", {"a": ["bug"] * 5})
    assert _delta(s, base, cand, "a") == "fixed"


def test_flaky_scenarios_are_not_counted_as_regressions_in_the_verdict():
    s = _set("a", "b")
    base = _arm("baseline", {"a": ["bug"] * 3, "b": ["bug"] * 3})
    cand = _arm("candidate", {"a": ["bug", "feature-request", "bug"], "b": ["bug"] * 3})
    c = Comparison(s, base, cand)
    assert c.summary()["flaky"] == 1
    assert c.summary()["broken"] == 0
    assert c.verdict() == "no change outside noise"


def test_a_real_regression_still_reaches_the_verdict():
    s = _set("a", "b")
    base = _arm("baseline", {"a": ["bug"] * 5, "b": ["bug"] * 5})
    cand = _arm("candidate", {"a": ["feature-request"] * 5, "b": ["bug"] * 5})
    assert Comparison(s, base, cand).verdict() == "regression"


def test_token_ratio_comes_from_the_batch_metrics():
    s = _set("a")
    base = _arm("baseline", {"a": ["bug"]})
    cand = _arm("candidate", {"a": ["bug"]})
    cand.batches = [{"total_tokens": 150}]
    assert Comparison(s, base, cand).summary()["token_ratio"] == 1.5


def _broken_arm(label, sid, error, n=3):
    arm = ArmResult(label, label)
    arm.runs = [ScenarioRun(sid, None, error) for _ in range(n)]
    arm.batches = [{"total_tokens": 10}]
    return arm


def test_a_batch_that_never_ran_is_incomplete_not_a_regression():
    """A harness outage, the gate catching the run, a subagent never receiving its item: none of these is a wrong answer.

    Scored as broken, one infrastructure failure becomes "this change broke 16 scenarios".
    """
    s = _set("a")
    base = _arm("baseline", {"a": ["bug"] * 3})
    cand = _broken_arm("candidate", "a", "503 from the harness")
    row = next(r for r in Comparison(s, base, cand).rows() if r["id"] == "a")
    assert row["delta"] == "incomplete"
    assert "503" in row["why"]


def test_an_incomplete_scenario_does_not_become_a_regression_verdict():
    s = _set("a", "b")
    base = _arm("baseline", {"a": ["bug"] * 3, "b": ["bug"] * 3})
    cand = _arm("candidate", {"b": ["bug"] * 3})
    cand.runs += [ScenarioRun("a", None, "503 from the harness") for _ in range(3)]
    c = Comparison(s, base, cand)
    assert c.summary()["incomplete"] == 1
    assert c.summary()["broken"] == 0
    assert c.verdict() == "incomplete"


def test_partial_failures_do_not_deflate_the_pass_rate():
    """With one of three runs lost to infrastructure, the denominator is 2, not 3."""
    s = _set("a")
    base = _arm("baseline", {"a": ["bug"] * 3})
    cand = _arm("candidate", {"a": ["bug", "bug"]})
    cand.runs.append(ScenarioRun("a", None, "connection reset"))
    row = next(r for r in Comparison(s, base, cand).rows() if r["id"] == "a")
    assert row["candidate"] == "2/2" and row["delta"] == "same"


def test_a_pass_rate_with_no_scored_runs_is_not_zero_percent():
    """With no run completing, "0%" is a lie: that is no data, not everything wrong."""
    s = _set("a")
    base = _arm("baseline", {"a": ["bug"] * 3})
    cand = _broken_arm("candidate", "a", "503 from the harness")
    md = Comparison(s, base, cand).to_markdown()
    assert "0/0 (n/a)" in md
    assert "(0%)" not in md
