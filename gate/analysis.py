"""Statistical reading of the pass-count matrix: the agent writes the code, the sandbox runs it.

This is not what TrueForge calls Code Mode. That means code in the sandbox calling
MCP tools through `mcp_client` and keeping intermediate results there. The pass-count
matrix is already in memory here, so routing it through a tool would buy nothing.

Why not hard-code the statistics: the question changes. Today it is whether the two
arms' intervals overlap; next time it may be whether one scenario was unstable to
begin with, or which scenarios carry the whole difference. Letting the agent write
code against the matrix in front of it beats enumerating every question in advance.

The line is drawn at the verdict. fixed / broken / flaky and the verdict itself are
settled by deterministic code in `checks.py` and `report.py` before anything reaches
this module; the sandbox only ever sees a decided matrix, and produces a reading that
cannot change the verdict. If it fails to run, the section is absent and the report
still prints.
"""
from __future__ import annotations

import json
import uuid

from . import harness

ASK = """Here is a pass-count matrix from an A/B evaluation of an agent spec. Each row is one
scenario; `baseline` passed out of `baseline_n` scored runs and `candidate` passed out
of `candidate_n`. The two counts can differ when runs failed to complete, and a
scenario with zero scored runs in an arm has no rate at all.

{payload}

Write Python and RUN it in your sandbox to compute, for each arm, the total passes,
the pass rate, and the Wilson 95% score interval; then report whether the two
intervals overlap, and list the scenarios where the two arms differ by more than one
repeat. Use only the standard library. Do not compute anything in your head. Report
what the code printed.

Answer with a short markdown section: one paragraph of interpretation, then a table.
No headings above level 4, no preamble."""


def payload(rows: list[dict]) -> str:
    """Each arm carries its own count: when one arm has failed runs, a shared n makes the sandbox compute the wrong rate."""
    return json.dumps([{"id": r["id"],
                        "baseline": r["baseline_pass"], "baseline_n": r["baseline_n"],
                        "candidate": r["candidate_pass"], "candidate_n": r["candidate_n"]}
                       for r in rows], indent=1)


def interpret(rows: list[dict], model: str = "google-gemini/gemini-3-6-flash") -> str | None:
    """Return a markdown section, or None when the sandbox or the model fails."""
    name = f"analyst-{uuid.uuid4().hex[:8]}"
    try:
        harness.create_agent(name, {
            "model": {"name": model},
            "instructions": "You are a data analyst. You compute every number by writing "
                            "and running code in your sandbox, never in your head.",
            "config": {"iteration_limit": 12,
                       "sandbox": {"enabled": True, "file_downloads": False}},
        })
        sid = harness.create_session(name)
        res = harness.run_turn(sid, ASK.format(payload=payload(rows)), stop_at_approval=False)
    except Exception:
        # The evaluation already ran for minutes. A broken add-on reading must not take those results down with it.
        return None
    if not res.output:
        return None
    started = any(e.type == "sandbox.created" for e in res.events)
    executed = any(e.type == "tool.response" for e in res.events)
    if not (started and executed):
        return None          # Talk with no tool response means the numbers were narrated, not computed
    return res.output.strip()
