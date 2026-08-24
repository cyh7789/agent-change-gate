"""判定邏輯的行為測試 —— 它決定比較表上的每一個數字。"""
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
