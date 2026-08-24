"""情境判定：agent 的輸出算不算通過。

判定要確定性。這個閘門的整個賣點是「數字可以重跑得到一樣的結果」，
判定一旦交給模型，同一份輸出兩次跑出不同分數，比較表就沒有意義了。
"""
from __future__ import annotations

import re


def _normalise(text: str) -> str:
    """去掉模型愛加的標點、程式碼框與贅字，只留判斷用的字面。"""
    t = text.strip().lower()
    t = t.strip("`*_ \n\t.")
    t = re.sub(r"^(the\s+)?(category\s+is\s+|answer:\s*)", "", t)
    return t.strip("`*_ \n\t.")


def check(output: str | None, expect: dict) -> tuple[bool, str]:
    """回傳 (是否通過, 原因)。原因會進逐筆表，讓人看得出為什麼算錯。"""
    if output is None:
        return False, "no output"
    if "equals_ignoring_case" in expect:
        want = expect["equals_ignoring_case"].strip().lower()
        got = _normalise(output)
        if got == want:
            return True, ""
        # 只在單行且長度接近時才承認「包含即算對」，避免長篇大論把每個類別都提到一次就通過
        if len(got) <= len(want) + 12 and want in got:
            return True, "matched within a short answer"
        return False, f"expected {want!r}, got {got[:60]!r}"
    if "contains" in expect:
        needle = expect["contains"].lower()
        return (needle in (output or "").lower()), f"expected to contain {needle!r}"
    return False, f"unsupported expectation: {list(expect)}"
