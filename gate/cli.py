"""Agent Change Gate: one candidate change, from evaluation to landing.

    python3 -m gate.cli --spec agents/issue-triage.json --candidate cand.json \
                        --scenarios scenarios/issue-triage.json --repo owner/name

The order is fixed: run the baseline, run the candidate, print the comparison, stop at the
approval gate, let a person decide, then land or discard. The comparison prints before the
gate, because whoever presses the button has to see the numbers first.
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
    ap.add_argument("--spec", required=True, help="the agent spec currently in effect")
    ap.add_argument("--candidate", required=True, help="the candidate agent spec")
    ap.add_argument("--scenarios", required=True, help="the frozen scenario set")
    ap.add_argument("--repo", help="GitHub repo to write back to once approved (owner/name)")
    ap.add_argument("--branch", default="change-gate/candidate")
    ap.add_argument("--batch-size", type=int, default=4,
                    help="scenarios per batch. The harness fans out within a batch; batches run one after another")
    ap.add_argument("--repeat", type=int, default=5,
                    help="runs per scenario per arm. The strongest split three repeats can produce "
                         "(0/3 vs 3/3) is p=0.1, which cannot support calling any scenario "
                         "fixed or broken, so the default is five")
    ap.add_argument("--no-analysis", action="store_true",
                    help="skip the sandbox statistical reading (the verdict is unaffected)")
    ap.add_argument("--yes", action="store_true", help="approve without asking (automation only; a person answers by default)")
    a = ap.parse_args(argv)

    scenarios = load(a.scenarios)          # a digest mismatch aborts here
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

    asked = {"n": 0}

    def ask(p) -> bool:
        asked["n"] += 1
        print(f"\n=== approval required ({asked['n']}) ===\n{p.tool_summary}")
        if a.yes:
            return True
        return input("allow this call? [y/N] ").strip().lower() == "y"

    landed, output = writeback.land(
        pending, ask, "Rejected at the change gate after reviewing the comparison report.")
    print(f"\n{output or ('(landed)' if landed else '(rejected)')}")
    print(f"({asked['n']} tool call(s) went through the gate)")
    return 0 if landed else 2


if __name__ == "__main__":
    sys.exit(main())
