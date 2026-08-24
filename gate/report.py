"""比較表：兩組跑完同一組情境之後，把差異寫成人看得懂的東西。

寫給要按核准鈕的人看。他要能回答三件事才敢按：哪些情境變了、成本差多少、
這份量尺有沒有被動過。所以逐筆結果、成本比、情境集 hash 三者缺一不可。
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

    def rows(self) -> list[dict]:
        by_id = {s.id: s for s in self.scenarios.scenarios}
        base = {r.scenario_id: r for r in self.baseline.runs}
        cand = {r.scenario_id: r for r in self.candidate.runs}
        out = []
        for sid in sorted(by_id):
            expect = by_id[sid].expect
            b_ok, b_why = check(base[sid].output if sid in base else None, expect)
            c_ok, c_why = check(cand[sid].output if sid in cand else None, expect)
            out.append({
                "id": sid,
                "baseline_pass": b_ok, "baseline_why": b_why,
                "candidate_pass": c_ok, "candidate_why": c_why,
                "delta": ("fixed" if c_ok and not b_ok else
                          "broken" if b_ok and not c_ok else "same"),
                "baseline_tokens": base[sid].metrics.get("total_tokens", 0) if sid in base else 0,
                "candidate_tokens": cand[sid].metrics.get("total_tokens", 0) if sid in cand else 0,
            })
        return out

    def summary(self) -> dict:
        rows = self.rows()
        n = len(rows)
        bp = sum(1 for r in rows if r["baseline_pass"])
        cp = sum(1 for r in rows if r["candidate_pass"])
        bt = self.baseline.total_tokens
        ct = self.candidate.total_tokens
        return {
            "scenarios": n,
            "baseline_pass": bp, "candidate_pass": cp,
            "baseline_rate": bp / n if n else 0.0,
            "candidate_rate": cp / n if n else 0.0,
            "fixed": sum(1 for r in rows if r["delta"] == "fixed"),
            "broken": sum(1 for r in rows if r["delta"] == "broken"),
            "baseline_tokens": bt, "candidate_tokens": ct,
            "token_ratio": (ct / bt) if bt else None,
        }

    def to_markdown(self) -> str:
        s = self.summary()
        rows = self.rows()
        verdict = ("regression" if s["candidate_pass"] < s["baseline_pass"] else
                   "improvement" if s["candidate_pass"] > s["baseline_pass"] else "no change")
        ratio = f"{s['token_ratio']:.2f}×" if s["token_ratio"] else "n/a"
        lines = [
            "## Change Gate report",
            "",
            f"**Verdict: {verdict}.** {s['candidate_pass']}/{s['scenarios']} passed, "
            f"baseline {s['baseline_pass']}/{s['scenarios']}. "
            f"{s['fixed']} fixed, {s['broken']} broken. Token cost {ratio} of baseline.",
            "",
            "| | baseline | candidate |",
            "|---|---|---|",
            f"| passed | {s['baseline_pass']}/{s['scenarios']} ({s['baseline_rate']:.0%}) | "
            f"{s['candidate_pass']}/{s['scenarios']} ({s['candidate_rate']:.0%}) |",
            f"| total tokens | {s['baseline_tokens']:,} | {s['candidate_tokens']:,} |",
            "",
            "### Per scenario",
            "",
            "| scenario | baseline | candidate | change | why |",
            "|---|---|---|---|---|",
        ]
        mark = {True: "pass", False: "fail"}
        for r in rows:
            why = r["candidate_why"] or r["baseline_why"] or ""
            lines.append(f"| `{r['id']}` | {mark[r['baseline_pass']]} | {mark[r['candidate_pass']]} | "
                         f"{r['delta']} | {why[:70]} |")
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
