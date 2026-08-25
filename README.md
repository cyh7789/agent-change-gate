# Agent Change Gate

Changing an agent's prompt is a deploy. This is the thing that stands between the
change and production: it re-runs a frozen scenario set against both the current
spec and the candidate, tells the difference between a real regression and model
noise, and then stops — a human decides whether the change lands on GitHub.

Built on the TrueForge harness for the WeMakeDevs TrueForge Hackathon.

## The problem it solves

Someone rewrites a line of an agent's instructions and ships it. Nothing is red,
because nothing was measuring. The failure shows up a week later as "the bot has
been mislabelling things", and by then nobody can say which edit did it.

The obvious answer — run both versions and compare — breaks on first contact:

- **The model is not deterministic.** Running the same spec against the same 16
  scenarios three times gave 15/16, 14/16, 14/16, and one scenario (`issue-332082`)
  answered pass, fail, fail. Compare single runs and you will report noise as a
  regression.
- **The measuring stick drifts.** If the scenario set can be edited between runs,
  the comparison means nothing. Here it is digest-frozen: the digest is recomputed
  from the contents on every run, and a mismatch refuses the run.
- **"Looks better" is not a decision.** The report has to reach the person who
  presses the button, before they press it.

## How it uses the harness

The gate is not a script that calls an LLM API. The harness does the work:

| | |
|---|---|
| **Subagents** | Scenarios are handed out in batches; the coordinator gives **each scenario to its own subagent**, inheriting the spec under test. Fan-out, scheduling and context isolation are the harness's job, not a thread pool's. |
| **Code Mode** | The statistical read of the results (Wilson intervals, per-scenario stability) is **code the agent writes and runs in its sandbox**. The verdict is not: that stays in deterministic Python, so the same outputs always score the same. |
| **Real tools via MCP** | Landing the change goes through the GitHub MCP server — branch, commit, pull request. No REST calls behind the harness's back. |
| **Human approval** | Because the write goes through a tool, `require_approval_for_tools: ["@write", "@destructive"]` catches it. The run pauses, prints the report, and waits. |
| **Resumable sessions** | A batch takes minutes, and the turn keeps running on the server if the connection drops. `run_turn` resumes from the last sequence id it saw (`after_sequence_number` is an exclusive cursor), so a drop costs no tokens and loses no events — `probe/reconnect.py` cuts a live connection and shows the turn completing anyway. |

Why the fan-out matters: calling the harness from a `ThreadPoolExecutor` treats it
as an HTTP endpoint. Handing the batch to subagents is the harness doing the work.
The mapping from answer back to scenario goes through the thread id and an item
marker — subagents finish in a different order than they were spawned, and pairing
them by order silently attaches answers to the wrong scenarios.

## What it found on its first real run

The candidate spec spelled out the classification criteria that the baseline left
implicit — the kind of edit nobody would think to test. It made one scenario worse
(a bug report became a feature-request) and cost 4% more tokens for it.

That is the whole point: the report existed before anyone had to have an opinion.

## Usage

```bash
python3 -m gate.cli \
  --spec agents/issue-triage.json \
  --candidate agents/issue-triage.candidate.json \
  --scenarios scenarios/issue-triage.json \
  --repo owner/name
```

Without `--repo` it evaluates and stops. With it, the run pauses at the approval
gate; answering anything but `y` rejects the tool call and **nothing reaches
GitHub** — verified with `gh api branches` and `gh pr list` after a rejection.

Flags: `--repeat` (runs per scenario per arm, default 3 — one run cannot separate
noise from a regression), `--batch-size` (scenarios per subagent fan-out),
`--no-analysis` (skip the sandbox statistical read).

## The scenario set

16 issues from `microsoft/vscode`, labelled by the maintainers, split 8 bug /
8 feature-request. `scripts/freeze_scenarios.py` builds it; `gate/scenarios.py`
verifies the digest on load and refuses to run against an edited set.

Scoring is deterministic string checking (`gate/checks.py`), never a model judging
a model: a grader that disagrees with itself between runs makes the comparison
worthless.

## Layout

```
gate/harness.py     TrueForge HTTP + SSE client, resume cursor, approval decisions
gate/fanout.py      hands a batch of scenarios to subagents, maps answers back
gate/runner.py      runs one arm over the frozen set
gate/checks.py      deterministic pass/fail
gate/report.py      comparison, flaky classification, verdict, markdown
gate/analysis.py    Code Mode statistical read (sandbox)
gate/writeback.py   GitHub MCP write-back behind the approval gate
probe/              standalone scripts that verify the harness capabilities used here
```

`python3 -m pytest tests -q`

## License

MIT
