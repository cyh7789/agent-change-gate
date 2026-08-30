"""Before a scenario is called fixed or broken, the difference has to survive a test.

This came from four three-repeat evaluations of the same pair of specs disagreeing: three said
no change, one said improvement. The one that flipped had `issue-332082` at baseline 0/3 and
candidate 3/3. Neither arm partially passed, so the flaky rule could not see it, and the change
was credited with a fix.

The sandbox statistics from that same run said the Wilson intervals overlapped and the
difference was not significant at 95%.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from gate.report import Comparison, fisher_exact_p
from gate.runner import ArmResult, ScenarioRun
from gate.scenarios import Scenario, ScenarioSet


def _set(*ids):
    return ScenarioSet(source="test", revision="test", digest="d" * 64,
                       scenarios=[Scenario(i, "p", {"equals_ignoring_case": "bug"}) for i in ids])


def _arm(label, outputs):
    arm = ArmResult(label=label, agent_name=label)
    arm.runs = [ScenarioRun(sid, o) for sid, outs in outputs.items() for o in outs]
    arm.batches = [{"total_tokens": 100}]
    return arm


def _delta(n_base, n_cand, sid="a"):
    s = _set(sid)
    base = _arm("baseline", {sid: n_base})
    cand = _arm("candidate", {sid: n_cand})
    return next(r for r in Comparison(s, base, cand).rows() if r["id"] == sid)


@pytest.mark.parametrize("bp,bn,cp,cn,want", [
    (0, 3, 3, 3, 0.1000),      # the strongest split three repeats can produce, still not significant
    (0, 4, 4, 4, 0.0286),
    (0, 5, 5, 5, 0.0079),
    (1, 3, 3, 3, 0.4000),
    (3, 3, 3, 3, 1.0000),      # no difference
])
def test_fisher_values(bp, bn, cp, cn, want):
    assert fisher_exact_p(bp, bn, cp, cn) == pytest.approx(want, abs=5e-4)


def test_three_runs_cannot_prove_a_single_scenario():
    """0/3 to 3/3 is the ceiling for three repeats, p=0.1. A direction with no evidence."""
    row = _delta(["feature-request"] * 3, ["bug"] * 3)
    assert row["delta"] == "unproven"
    assert "0.10" in row["why"]


def test_five_runs_can():
    row = _delta(["feature-request"] * 5, ["bug"] * 5)
    assert row["delta"] == "fixed"


def test_a_real_break_at_five_runs_is_broken():
    row = _delta(["bug"] * 5, ["feature-request"] * 5)
    assert row["delta"] == "broken"


def test_partial_passes_are_still_flaky_before_anything_else():
    """A partial pass is instability on its own, with no test needed."""
    row = _delta(["bug", "feature-request", "bug"], ["bug"] * 3)
    assert row["delta"] == "flaky"


def test_an_unproven_scenario_does_not_reach_the_verdict():
    s = _set("a", "b")
    base = _arm("baseline", {"a": ["feature-request"] * 3, "b": ["bug"] * 3})
    cand = _arm("candidate", {"a": ["bug"] * 3, "b": ["bug"] * 3})
    c = Comparison(s, base, cand)
    assert c.summary()["fixed"] == 0
    assert c.summary()["unproven"] == 1
    assert c.verdict() == "no change outside noise"


def test_an_unproven_row_keeps_its_p_value_in_the_reason():
    """The p value is the reason for unproven. Overwritten by a failure reason, the row has nothing explaining itself."""
    row = _delta(["feature-request"] * 3, ["bug", "bug", "bug"])
    assert row["delta"] == "unproven"
    assert "p=" in row["why"]


def test_an_unproven_break_keeps_its_p_value():
    """When the candidate fails everything (cp=0 < cn), the old code overwrote why with the first failure reason and lost the p value.

    3/3 down to 0/3 is the strongest regression three repeats can produce, and p is still 0.1:
    a direction with no evidence.
    """
    row = _delta(["bug"] * 3, ["feature-request"] * 3)
    assert row["delta"] == "unproven"
    assert "p=" in row["why"], f"the reason for unproven was overwritten: {row['why']!r}"


def test_direction_does_not_depend_on_the_arms_having_equal_run_counts():
    """The arms can have different numbers of scored runs once failures are excluded, so direction compares rates, not counts."""
    s = _set("a")
    base = _arm("baseline", {"a": ["bug"] * 3})
    cand = _arm("candidate", {"a": ["bug"] * 5})
    row = next(r for r in Comparison(s, base, cand).rows() if r["id"] == "a")
    assert row["delta"] == "same", "3/3 and 5/5 are the same pass rate and must not read as a change"
