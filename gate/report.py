"""比較表：兩組跑完同一組情境之後，把差異寫成人看得懂的東西。

寫給要按核准鈕的人看。他要能回答三件事才敢按：哪些情境真的變了、成本差多少、
這份量尺有沒有被動過。

「真的變了」需要跟雜訊分開。模型輸出不是確定性的：同一份 spec 連跑三次，
16 題裡有一題給出 pass/fail/fail。所以每個情境跑多次、比的是通過次數，
而任一組不穩定的情境標成 flaky，不算在變更帶來的差異裡。
"""
from __future__ import annotations

from dataclasses import dataclass
from math import comb

from .checks import check
from .runner import ArmResult
from .scenarios import ScenarioSet


ALPHA = 0.05


def fisher_exact_p(bp: int, bn: int, cp: int, cn: int) -> float:
    """兩組通過數差異的雙尾 Fisher exact p 值。

    純標準庫，確定性：同一組計數永遠得到同一個 p，判決才重跑得出來。

    值得記住的是它對次數的下限：三次重複能做到的最強差異是 0/3 對 3/3，p = 0.1，
    永遠過不了 0.05。要宣告某一題被修好或弄壞，每組至少要跑四次。
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
            moved = (cp > bp and bp < bn) or (bp > cp and cp < cn)
            p = fisher_exact_p(bp, bn, cp, cn)
            if flaky:
                delta = "flaky"
            elif not moved:
                delta = "same"
            elif p > ALPHA:
                # 有方向，沒有證據。四次執行裡有一次就是這樣被記成 improvement 的：
                # 一組 0/3、另一組 3/3，兩邊都不部分通過，flaky 看不見，而 p = 0.1。
                delta = "unproven"
            elif cp > bp:
                delta = "fixed"
            else:
                delta = "broken"
            why = ""
            if delta == "unproven":
                why = f"direction only, p={p:.2f} at {bn} and {cn} runs"
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
            "unproven": sum(1 for r in rows if r["delta"] == "unproven"),
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
        # 分母是 0 代表沒有資料，不是全錯。印成 0% 會讓一次故障讀起來像全軍覆沒。
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
