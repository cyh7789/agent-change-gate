"""宣告一個情境被修好或弄壞之前，那個差異要先撐得住檢定。

起因是同一組 spec 連跑四次三重複評測，判決不一致：三次 no change，一次 improvement。
翻掉的那次是 `issue-332082` 落在 baseline 0/3、candidate 3/3。兩邊都不是「部分通過」，
所以 flaky 規則看不見它，這個變更就被記了一筆修好。

而同一次執行的 sandbox 統計說 Wilson 區間重疊、95% 下不顯著。
"""
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
    (0, 3, 3, 3, 0.1000),      # 三次重複能做到的最強差異，仍然不顯著
    (0, 4, 4, 4, 0.0286),
    (0, 5, 5, 5, 0.0079),
    (1, 3, 3, 3, 0.4000),
    (3, 3, 3, 3, 1.0000),      # 沒有差異
])
def test_fisher_values(bp, bn, cp, cn, want):
    assert fisher_exact_p(bp, bn, cp, cn) == pytest.approx(want, abs=5e-4)


def test_three_runs_cannot_prove_a_single_scenario():
    """0/3 → 3/3 是三次重複的極限，p=0.1。有方向，沒有證據。"""
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
    """部分通過就是不穩定，不必等檢定。"""
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
    """unproven 的理由是那個 p 值。被失敗原因蓋掉的話，這一列就沒有東西解釋它為什麼是 unproven。"""
    row = _delta(["feature-request"] * 3, ["bug", "bug", "bug"])
    assert row["delta"] == "unproven"
    assert "p=" in row["why"]


def test_an_unproven_break_keeps_its_p_value():
    """候選全錯的時候（cp=0 < cn），舊碼會把 why 改寫成第一個失敗原因，p 值就不見了。

    3/3 掉到 0/3 是三次重複能做到的最強退步，p 還是 0.1：有方向，沒有證據。
    """
    row = _delta(["bug"] * 3, ["feature-request"] * 3)
    assert row["delta"] == "unproven"
    assert "p=" in row["why"], f"unproven 的理由被蓋掉了：{row['why']!r}"


def test_direction_does_not_depend_on_the_arms_having_equal_run_counts():
    """兩組跑成的次數可以不同（有些 run 失敗被排除），方向要看通過率不是通過次數。"""
    s = _set("a")
    base = _arm("baseline", {"a": ["bug"] * 3})
    cand = _arm("candidate", {"a": ["bug"] * 5})
    row = next(r for r in Comparison(s, base, cand).rows() if r["id"] == "a")
    assert row["delta"] == "same", "3/3 與 5/5 的通過率相同，不該被當成有變化"
