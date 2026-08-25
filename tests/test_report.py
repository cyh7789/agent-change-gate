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
    # 五次：三次重複的差異過不了檢定，見 test_significance.py
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
    """harness 掛掉、核准閘攔下、subagent 沒接題，都不是模型答錯。

    算成 broken 的話，一次基礎設施故障就會變成「這個變更造成 16 個退步」。
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
    """三次裡有一次是基礎設施錯，分母要是 2，不是 3。"""
    s = _set("a")
    base = _arm("baseline", {"a": ["bug"] * 3})
    cand = _arm("candidate", {"a": ["bug", "bug"]})
    cand.runs.append(ScenarioRun("a", None, "connection reset"))
    row = next(r for r in Comparison(s, base, cand).rows() if r["id"] == "a")
    assert row["candidate"] == "2/2" and row["delta"] == "same"


def test_a_pass_rate_with_no_scored_runs_is_not_zero_percent():
    """一次都沒跑成的時候「0%」是謊；那是沒有資料，不是全錯。"""
    s = _set("a")
    base = _arm("baseline", {"a": ["bug"] * 3})
    cand = _broken_arm("candidate", "a", "503 from the harness")
    md = Comparison(s, base, cand).to_markdown()
    assert "0/0 (n/a)" in md
    assert "(0%)" not in md
