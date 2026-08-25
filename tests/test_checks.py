"""判定邏輯的行為測試：它決定比較表上的每一個數字。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gate.checks import check

EXPECT = {"equals_ignoring_case": "bug"}


def test_exact_answer_passes():
    assert check("bug", EXPECT)[0]


def test_model_decoration_is_tolerated():
    for raw in ("Bug", "  bug.", "`bug`", "**bug**", "The category is bug", "Answer: bug"):
        ok, why = check(raw, EXPECT)
        assert ok, f"{raw!r} should pass, got {why}"


def test_wrong_category_fails_with_reason():
    ok, why = check("feature-request", EXPECT)
    assert not ok
    assert "expected 'bug'" in why


def test_essay_mentioning_every_category_does_not_pass():
    essay = ("This issue could be seen as a bug, though some maintainers would file it "
             "as a feature-request, and it also touches documentation.")
    ok, _ = check(essay, EXPECT)
    assert not ok, "long answers that name every category must not count as correct"


def test_missing_output_fails():
    ok, why = check(None, EXPECT)
    assert not ok and why == "no output"


# --- 比較表：雜訊必須跟真的變化分開 ---

from dataclasses import dataclass       # noqa: E402

from gate.report import Comparison      # noqa: E402
from gate.runner import ArmResult, ScenarioRun  # noqa: E402
from gate.scenarios import Scenario, ScenarioSet  # noqa: E402


def _set(*ids):
    return ScenarioSet(source="test", revision="test", digest="d",
                       scenarios=tuple(Scenario(i, "p", {"equals_ignoring_case": "bug"}) for i in ids))


def _arm(label, outputs):
    """outputs: {scenario_id: [每次執行的輸出]}"""
    runs = [ScenarioRun(sid, o) for sid, outs in outputs.items() for o in outs]
    return ArmResult(label=label, agent_name=label, runs=runs)


def test_a_scenario_unstable_in_one_arm_is_flaky_not_broken():
    sc = _set("s1")
    base = _arm("b", {"s1": ["bug", "bug", "bug"]})
    cand = _arm("c", {"s1": ["bug", "feature-request", "bug"]})
    row = Comparison(sc, base, cand).rows()[0]
    assert row["delta"] == "flaky"
    assert Comparison(sc, base, cand).verdict() == "no change outside noise"


def test_consistently_failing_candidate_is_broken():
    # 五次而非三次：三次重複最強只能做到 p=0.1，撐不起 broken 的宣告。
    sc = _set("s1")
    base = _arm("b", {"s1": ["bug"] * 5})
    cand = _arm("c", {"s1": ["feature-request"] * 5})
    c = Comparison(sc, base, cand)
    assert c.rows()[0]["delta"] == "broken"
    assert c.verdict() == "regression"


def test_consistently_fixed_candidate_is_improvement():
    sc = _set("s1")
    base = _arm("b", {"s1": ["feature-request"] * 5})
    cand = _arm("c", {"s1": ["bug"] * 5})
    c = Comparison(sc, base, cand)
    assert c.rows()[0]["delta"] == "fixed"
    assert c.verdict() == "improvement"


def test_flaky_baseline_does_not_become_a_fix():
    sc = _set("s1")
    base = _arm("b", {"s1": ["bug", "feature-request", "bug"]})
    cand = _arm("c", {"s1": ["bug"] * 3})
    assert Comparison(sc, base, cand).rows()[0]["delta"] == "flaky"
