"""Agent Change Gate：一次候選變更，從評測到落地。

    python3 -m gate.cli --spec agents/issue-triage.json --candidate cand.json \
                        --scenarios scenarios/issue-triage.json --repo owner/name

流程固定：跑基準 → 跑候選 → 出比較表 → 停在核准閘 → 人決定 → 落地或作廢。
比較表在核准之前就印出來，因為要按鈕的人得先看到數字。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import analysis, report, runner, writeback
from .scenarios import load


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gate")
    ap.add_argument("--spec", required=True, help="目前生效的 agent spec")
    ap.add_argument("--candidate", required=True, help="候選 agent spec")
    ap.add_argument("--scenarios", required=True, help="凍結情境集")
    ap.add_argument("--repo", help="核准後要寫回的 GitHub repo（owner/name）")
    ap.add_argument("--branch", default="change-gate/candidate")
    ap.add_argument("--batch-size", type=int, default=4,
                    help="一批交給幾個 subagent。批內由 harness 扇出，批之間序列跑")
    ap.add_argument("--repeat", type=int, default=3,
                    help="每個情境每組跑幾次。模型不是確定性的，跑一次分不出雜訊與真的退步")
    ap.add_argument("--no-analysis", action="store_true",
                    help="跳過 sandbox 統計解讀（判決不受影響）")
    ap.add_argument("--yes", action="store_true", help="不詢問直接核准（僅供自動化，預設要人回答）")
    a = ap.parse_args(argv)

    scenarios = load(a.scenarios)          # digest 不符會在這裡中止
    base_spec = json.loads(Path(a.spec).read_text())
    cand_spec = json.loads(Path(a.candidate).read_text())

    print(f"scenarios: {len(scenarios)} from {scenarios.source}")
    print(f"digest: {scenarios.digest[:16]}…\n")

    base = runner.run_arm("baseline", base_spec, scenarios, a.batch_size, a.repeat)
    print(f"baseline done: {base.completed}/{len(base.runs)} produced output, {base.total_tokens:,} tokens")
    cand = runner.run_arm("candidate", cand_spec, scenarios, a.batch_size, a.repeat)
    print(f"candidate done: {cand.completed}/{len(cand.runs)} produced output, {cand.total_tokens:,} tokens\n")

    comparison = report.Comparison(scenarios, base, cand)
    read = None if a.no_analysis else analysis.interpret(comparison.rows())
    if read is None and not a.no_analysis:
        print("(statistical read skipped: the sandbox analyst produced nothing)")
    md = comparison.to_markdown(read)
    print(md)
    Path("change-gate-report.md").write_text(md + "\n")

    if not a.repo:
        print("\n(no --repo given: evaluation only, nothing to land)")
        return 0

    pending = writeback.propose(
        repo=a.repo, branch=a.branch, path=a.spec,
        content=json.dumps(cand_spec, indent=1),
        title=f"Agent spec change: {comparison.summary()['candidate_pass']}/{len(scenarios)} on frozen scenarios",
        report_md=md)
    if pending is None:
        print("\nno approval was requested: the write-back agent did not reach a write tool")
        return 1

    print(f"\n=== approval required ===\n{pending.tool_summary}")
    decision = "y" if a.yes else input("land this change? [y/N] ").strip().lower()
    if decision == "y":
        print(writeback.approve(pending) or "(approved)")
        return 0
    print(writeback.reject(pending, "Rejected at the change gate after reviewing the comparison report.")
          or "(rejected)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
