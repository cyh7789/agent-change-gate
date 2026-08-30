"""The comparison: what changed once both arms have been through the same scenario set.

It is written for whoever presses the approval button. Three questions have to be answerable
before anyone dares: which scenarios really moved, what it costs, and whether the measuring
stick has been touched.

"Really moved" has to be separated from noise. Model output is not deterministic: measured on
three consecutive runs of one spec, 1 of the 16 scenarios answered pass/fail/fail. So each
scenario runs several times and pass counts are compared, and a scenario unstable in either
arm is marked flaky and is not attributed to the change.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import comb

from .checks import check
from .runner import ArmResult
from .scenarios import ScenarioSet


ALPHA = 0.05


def fisher_exact_p(bp: int, bn: int, cp: int, cn: int) -> float:
    """Two-tailed Fisher exact p for the difference in pass counts between the arms.

    Standard library only, and deterministic: the same counts always give the same p, which is
    what lets the verdict survive a re-run.

    The floor it puts on the repeat count is worth remembering. The strongest split three
    repeats can produce is 0/3 against 3/3, p = 0.1, which never clears 0.05. Calling any
    scenario fixed or broken takes at least four runs per arm.
    """
    a, b, c, d = bp, bn - bp, cp, cn - cp
    n, row1, row2, col1 = bn + cn, a + b, c + d, a + c
    if not n or not row1 or not row2:
        return 1.0
    observed = comb(row1, a) * comb(row2, c) / comb(n, col1)
    total = 0.0
    for k in range(max(0, col1 - row2), min(row1, col1) + 1):
        p = comb(row1, k) * comb(row2, col1 - k) / comb(n, col1)
        if p <= observed + 1e-12:
            total += p
    return min(total, 1.0)


@dataclass
class Comparison:
    scenarios: ScenarioSet
    baseline: ArmResult
    candidate: ArmResult

    @staticmethod
    def _scored(arm: ArmResult, sid: str) -> list:
        """Only runs that actually completed are scored.

        A harness outage, the approval gate catching the run mid-evaluation, a subagent never
        receiving its item: those are evaluations that did not happen, not wrong answers. Let
        them in and one infrastructure failure gets reported as a regression across the set.
        """
        return [r for r in arm.for_scenario(sid) if r.error is None]

    def _passes(self, arm: ArmResult, sid: str, expect: dict) -> int:
        return sum(1 for r in self._scored(arm, sid) if check(r.output, expect)[0])

    @staticmethod
    def _first_error(arm: ArmResult, sid: str) -> str:
        for r in arm.for_scenario(sid):
            if r.error:
                return r.error
        return "no run completed"

    def rows(self) -> list[dict]:
        rows = []
        for s in sorted(self.scenarios.scenarios, key=lambda x: x.id):
            bn = len(self._scored(self.baseline, s.id))
            cn = len(self._scored(self.candidate, s.id))
            bp = self._passes(self.baseline, s.id, s.expect)
            cp = self._passes(self.candidate, s.id, s.expect)
            if bn == 0 or cn == 0:
                arm = self.baseline if bn == 0 else self.candidate
                rows.append({"id": s.id, "baseline": f"{bp}/{bn}", "candidate": f"{cp}/{cn}",
                             "delta": "incomplete", "why": self._first_error(arm, s.id),
                             "baseline_pass": bp, "baseline_n": bn,
                             "candidate_pass": cp, "candidate_n": cn})
                continue
            flaky = (0 < bp < bn) or (0 < cp < cn)
            # Direction compares rates, not counts: the arms can have different numbers of
            # scored runs once failures are excluded, and 3/3 against 5/5 is the same result,
            # not two extra passes.
            moved = (bp / bn) != (cp / cn)
            p = fisher_exact_p(bp, bn, cp, cn)
            if flaky:
                delta = "flaky"
            elif not moved:
                delta = "same"
            elif p > ALPHA:
                # A direction with no evidence. One of four runs was recorded as an
                # improvement exactly this way: 0/3 against 3/3, neither arm partially
                # passing so nothing looked flaky, and p = 0.1.
                delta = "unproven"
            elif cp / cn > bp / bn:
                delta = "fixed"
            else:
                delta = "broken"
            why = ""
            if cp < cn:
                for r in self._scored(self.candidate, s.id):
                    ok, reason = check(r.output, s.expect)
                    if not ok:
                        why = reason
                        break
            if delta == "unproven":
                # The p value is why this row exists; a failure reason must not overwrite it.
                why = f"direction only, p={p:.2f} at {bn} and {cn} runs"
            rows.append({"id": s.id, "baseline": f"{bp}/{bn}", "candidate": f"{cp}/{cn}",
                         "delta": delta, "why": why,
                         "baseline_pass": bp, "baseline_n": bn,
                         "candidate_pass": cp, "candidate_n": cn})
        return rows

    def summary(self) -> dict:
        rows = self.rows()
        bt, ct = self.baseline.total_tokens, self.candidate.total_tokens
        return {
            "scenarios": len(rows),
            "repeats": max(self.baseline.repeats, 1),
            "baseline_pass": sum(r["baseline_pass"] for r in rows),
            "baseline_total": sum(r["baseline_n"] for r in rows),
            "candidate_pass": sum(r["candidate_pass"] for r in rows),
            "candidate_total": sum(r["candidate_n"] for r in rows),
            "fixed": sum(1 for r in rows if r["delta"] == "fixed"),
            "broken": sum(1 for r in rows if r["delta"] == "broken"),
            "flaky": sum(1 for r in rows if r["delta"] == "flaky"),
            "unproven": sum(1 for r in rows if r["delta"] == "unproven"),
            "incomplete": sum(1 for r in rows if r["delta"] == "incomplete"),
            "baseline_tokens": bt, "candidate_tokens": ct,
            "token_ratio": (ct / bt) if bt else None,
        }

    def verdict(self) -> str:
        s = self.summary()
        if s["incomplete"]:
            return "incomplete"          # a scenario never completed, so these numbers cannot settle anything
        if s["broken"] and not s["fixed"]:
            return "regression"
        if s["fixed"] and not s["broken"]:
            return "improvement"
        if s["fixed"] and s["broken"]:
            return "mixed"
        return "no change outside noise"

    def to_markdown(self, analysis: str | None = None) -> str:
        s, rows = self.summary(), self.rows()
        ratio = f"{s['token_ratio']:.2f}×" if s["token_ratio"] else "n/a"
        # A zero denominator means no data, not everything wrong. Printing 0% makes one outage
        # read as a total wipe-out.
        rate = lambda p, t: f"({p / t:.0%})" if t else "(n/a)"
        br, cr = rate(s["baseline_pass"], s["baseline_total"]), rate(s["candidate_pass"], s["candidate_total"])
        lines = [
            "## Change Gate report",
            "",
            f"**Verdict: {self.verdict()}.** {s['fixed']} fixed, {s['broken']} broken, "
            f"{s['flaky']} flaky (unstable in at least one arm, not attributed to the change), "
            f"{s['unproven']} unproven (moved, but not past a Fisher exact test at "
            f"p<={ALPHA}), {s['incomplete']} incomplete (never ran to completion, not scored). "
            f"Token cost {ratio} of baseline.",
            "",
            f"Every scenario ran {s['repeats']}× per arm, because the model is not deterministic: "
            "a scenario that passes once and fails once tells you nothing about the change.",
            "",
            "| | baseline | candidate |",
            "|---|---|---|",
            f"| passed | {s['baseline_pass']}/{s['baseline_total']} {br} | "
            f"{s['candidate_pass']}/{s['candidate_total']} {cr} |",
            f"| total tokens | {s['baseline_tokens']:,} | {s['candidate_tokens']:,} |",
            "",
            "### Per scenario",
            "",
            "| scenario | baseline | candidate | change | why |",
            "|---|---|---|---|---|",
        ]
        for r in rows:
            lines.append(f"| `{r['id']}` | {r['baseline']} | {r['candidate']} | {r['delta']} | {r['why'][:60]} |")
        if analysis:
            lines += [
                "",
                "### Statistical read",
                "",
                analysis,
                "",
                "_Computed by code the agent wrote and ran in its sandbox, from the pass "
                "counts above. The verdict itself is not: it comes from the deterministic "
                "checks, so the same outputs always score the same._",
            ]
        lines += [
            "",
            "### Measuring stick",
            "",
            f"- source: {self.scenarios.source}",
            f"- revision: {self.scenarios.revision[:120]}",
            f"- digest: `{self.scenarios.digest}`",
            "",
            "The digest is recomputed from the scenario contents on every run. "
            "Editing a prompt or adding a scenario changes it and the run is refused.",
        ]
        return "\n".join(lines)
