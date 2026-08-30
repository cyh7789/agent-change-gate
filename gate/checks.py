"""Scenario checks: whether an agent's output counts as a pass.

The check has to be deterministic. This gate's whole claim is that the numbers
survive a re-run, and the moment a model decides the score, the same output can
be graded two different ways and the comparison table means nothing.
"""
from __future__ import annotations

import re


def _normalise(text: str) -> str:
    """Strip the punctuation, code fences and filler models like to add, leaving the answer."""
    t = text.strip().lower()
    t = t.strip("`*_ \n\t.")
    t = re.sub(r"^(the\s+)?(category\s+is\s+|answer:\s*)", "", t)
    return t.strip("`*_ \n\t.")


def check(output: str | None, expect: dict) -> tuple[bool, str]:
    """Return (passed, reason). The reason goes in the per-scenario table so a failure is readable."""
    if output is None:
        return False, "no output"
    if "equals_ignoring_case" in expect:
        want = expect["equals_ignoring_case"].strip().lower()
        got = _normalise(output)
        if got == want:
            return True, ""
        # Accept a substring match only on a short single-line answer, so an essay that
        # mentions every category on its way through does not pass on all of them.
        if len(got) <= len(want) + 12 and want in got:
            return True, "matched within a short answer"
        return False, f"expected {want!r}, got {got[:60]!r}"
    if "contains" in expect:
        needle = expect["contains"].lower()
        return (needle in (output or "").lower()), f"expected to contain {needle!r}"
    return False, f"unsupported expectation: {list(expect)}"
