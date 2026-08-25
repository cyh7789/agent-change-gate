"""比較表：兩組跑完同一組情境之後，把差異寫成人看得懂的東西。

寫給要按核准鈕的人看。他要能回答三件事才敢按：哪些情境真的變了、成本差多少、
這份量尺有沒有被動過。

「真的變了」需要跟雜訊分開。模型輸出不是確定性的：同一份 spec 連跑三次，
16 題裡有一題給出 pass/fail/fail。所以每個情境跑多次、比的是通過次數，
而任一組不穩定的情境標成 flaky，不算在變更帶來的差異裡。
"""
from __future__ import annotations

from dataclasses import dataclass

from .checks import check
from .runner import ArmResult
from .scenarios import ScenarioSet


@dataclass
class Comparison:
    scenarios: ScenarioSet
    baseline: ArmResult
    candidate: ArmResult

    @staticmethod
    def _scored(arm: ArmResult, sid: str) -> list:
        """只有真的跑到底的那幾次算數。

        harness 掛掉、核准閘在評測中攔下、subagent 沒接到題，這些是評測沒跑成，
        不是模型答錯。混進來的話，一次基礎設施故障會被報成整組退步。
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
            if flaky:
                delta = "flaky"
            elif cp == cn and bp < bn:
                delta = "fixed"
            elif bp == bn and cp < cn:
                delta = "broken"
            else:
                delta = "same"
            why = ""
            if cp < cn:
                for r in self._scored(self.candidate, s.id):
                    ok, reason = check(r.output, s.expect)
                    if not ok:
                        why = reason
                        break
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
            "incomplete": sum(1 for r in rows if r["delta"] == "incomplete"),
            "baseline_tokens": bt, "candidate_tokens": ct,
            "token_ratio": (ct / bt) if bt else None,
        }

    def verdict(self) -> str:
        s = self.summary()
        if s["incomplete"]:
            return "incomplete"          # 有情境根本沒跑成，不能拿這組數字下結論
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
        br = s["baseline_pass"] / s["baseline_total"] if s["baseline_total"] else 0
        cr = s["candidate_pass"] / s["candidate_total"] if s["candidate_total"] else 0
        lines = [
            "## Change Gate report",
            "",
            f"**Verdict: {self.verdict()}.** {s['fixed']} fixed, {s['broken']} broken, "
            f"{s['flaky']} flaky (unstable in at least one arm, not attributed to the change), "
            f"{s['incomplete']} incomplete (never ran to completion, not scored). "
            f"Token cost {ratio} of baseline.",
            "",
            f"Every scenario ran {s['repeats']}× per arm, because the model is not deterministic: "
            "a scenario that passes once and fails once tells you nothing about the change.",
            "",
            "| | baseline | candidate |",
            "|---|---|---|",
            f"| passed | {s['baseline_pass']}/{s['baseline_total']} ({br:.0%}) | "
            f"{s['candidate_pass']}/{s['candidate_total']} ({cr:.0%}) |",
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
