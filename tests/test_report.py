"""比較表的行為測試。

咬的是這份報表存在的理由：把雜訊跟真的退步分開。同一份 spec 對同一組情境連跑
三次是 15/16、14/16、14/16，其中 issue-332082 給出 pass/fail/fail。單看一次
就會把那一題報成變更造成的退步。
"""
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
    """answers: {scenario_id: [每次跑的輸出]}"""
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
    s = _set("a")
    base = _arm("baseline", {"a": ["bug", "bug", "bug"]})
    cand = _arm("candidate", {"a": ["feature-request"] * 3})
    assert _delta(s, base, cand, "a") == "broken"


def test_consistently_gained_is_fixed():
    s = _set("a")
    base = _arm("baseline", {"a": ["feature-request"] * 3})
    cand = _arm("candidate", {"a": ["bug", "bug", "bug"]})
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
    base = _arm("baseline", {"a": ["bug"] * 3, "b": ["bug"] * 3})
    cand = _arm("candidate", {"a": ["feature-request"] * 3, "b": ["bug"] * 3})
    assert Comparison(s, base, cand).verdict() == "regression"


def test_token_ratio_comes_from_the_batch_metrics():
    s = _set("a")
    base = _arm("baseline", {"a": ["bug"]})
    cand = _arm("candidate", {"a": ["bug"]})
    cand.batches = [{"total_tokens": 150}]
    assert Comparison(s, base, cand).summary()["token_ratio"] == 1.5


def test_a_scenario_with_no_output_reports_why():
    s = _set("a")
    base = _arm("baseline", {"a": ["bug"] * 3})
    cand = ArmResult("candidate", "candidate")
    cand.runs = [ScenarioRun("a", None, "no subagent returned an answer for this item")] * 3
    cand.batches = [{"total_tokens": 10}]
    row = next(r for r in Comparison(s, base, cand).rows() if r["id"] == "a")
    assert row["delta"] == "broken" and "no output" in row["why"]
