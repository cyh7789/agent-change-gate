# Agent Change Gate

Changing an agent's prompt is a deploy. This is the thing that stands between the
change and production: it re-runs a frozen scenario set against both the current
spec and the candidate, tells the difference between a real regression and model
noise, and then stops so a human decides whether the change lands on GitHub.

Built on the TrueForge harness for the WeMakeDevs TrueForge Hackathon.

## The problem it solves

Someone rewrites a line of an agent's instructions and ships it. Nothing is red,
because nothing was measuring. The failure shows up a week later as "the bot has
been mislabelling things", and by then nobody can say which edit did it.

The obvious answer, run both versions and compare, breaks on first contact:

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
| **Subagents** | Scenarios are handed out in batches; the coordinator gives **each scenario to its own subagent**. Fan-out, scheduling and context isolation are the harness's job, not a thread pool's. |
| **Code Mode** | The statistical read of the results (Wilson intervals, per-scenario stability) is **code the agent writes and runs in its sandbox**. The verdict is not: that stays in deterministic Python, so the same outputs always score the same. |
| **Real tools via MCP** | Landing the change goes through the GitHub MCP server: branch, commit, pull request. No REST calls behind the harness's back. |
| **Human approval** | Because the write goes through a tool, `require_approval_for_tools: ["@write", "@destructive"]` catches it. The run pauses, prints the report, and waits. |
| **Resumable sessions** | A batch takes minutes, and the turn keeps running on the server if the connection drops. `run_turn` resumes from the last sequence id it saw (`after_sequence_number` is an exclusive cursor), so a drop costs no tokens and loses no events. `probe/reconnect.py` cuts a live connection and shows the turn completing anyway. |

Why the fan-out matters: calling the harness from a `ThreadPoolExecutor` treats it
as an HTTP endpoint. Handing the batch to subagents is the harness doing the work.
The mapping from answer back to scenario goes through the thread id and an item
marker. Subagents finish in a different order than they were spawned, and pairing
them by order silently attaches answers to the wrong scenarios.

**Subagents do not inherit the parent agent's instructions.** Measured, not
assumed: a spec whose instructions demand the token `ZX9QQ` at the end of every
reply produced it only in the coordinator's own output, never in either
subagent's. An earlier version of this code took that inheritance for granted and
compared two specs with neither of them in effect. The spec under test now travels
with each item, and the coordinator carries dispatch rules only, so it cannot
answer under the spec itself. `probe/fanout.py` re-runs the fingerprint check.

## What it says about a real change

The candidate spec spells out the classification criteria the baseline leaves
implicit, the kind of edit nobody would think to test. Three runs per scenario,
16 scenarios, both specs:

```
Verdict: no change outside noise. 0 fixed, 0 broken, 1 flaky. Token cost 1.01x.

passed        baseline 43/48 (90%)    candidate 42/48 (88%)
issue-332082  baseline 1/3            candidate 0/3            flaky
```

That one scenario is the whole argument. Run each spec once and you get baseline
pass, candidate fail, and a report saying the change broke it. Run three times and
it is a scenario the model cannot answer consistently under either spec, and
the change did nothing to it.

An earlier run of the same pair, before the fan-out moved to subagents, did surface
a real difference: the candidate turned one bug report into a feature-request and
cost 4% more tokens. Both readings came out of the gate rather than out of somebody's
impression of the diff, which is the point.

## Setup

Needs Python 3.11+, Node (for `npx`), the `gh` CLI logged in, and a Gemini API key.

```bash
# 1. run the harness (keeps its config in ~/Library/Application Support/trueforge)
npx -y @truefoundry/trueforge          # serves http://localhost:8790

# 2. point it at a model provider and the GitHub MCP server
GEMINI_API_KEY=... ./scripts/setup_harness.sh
#    -> model provider: ok / github mcp: ok / auth status: github=authenticated

# 3. check the pieces this project relies on
python3 -m pytest tests -q
python3 probe/fanout.py && python3 probe/reconnect.py && python3 probe/codemode.py
```

Re-running step 2 on an already-configured harness prints `already exists`, which
is fine. Set `TRUEFORGE_BASE` if the harness is not on `localhost:8790`. Another provider works too: edit the manifest in the script and the
`model.name` in `agents/*.json`.

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
GitHub**, verified with `gh api branches` and `gh pr list` after a rejection.

Flags: `--repeat` (runs per scenario per arm, default 3, because one run cannot separate
noise from a regression), `--batch-size` (scenarios per subagent fan-out),
`--no-analysis` (skip the sandbox statistical read).

### The console

```bash
python3 -m gate.web --spec agents/issue-triage.json \
                    --candidate agents/issue-triage.candidate.json \
                    --scenarios scenarios/issue-triage.json \
                    --repo owner/name
```

Same run with a page at `127.0.0.1:8791`: progress, the per-scenario table as it
fills in, the sandbox read, and the approval gate as a pair of buttons. Approving
is the only thing on the page that reaches the outside world, and every tool call
in the write-back comes back for its own decision. The evaluation itself is
unchanged, so the console adds a viewer, not a second code path.

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
gate/state.py       the run's live state, and the approval the console holds
gate/web.py         the console: progress, table, approve/reject
probe/              standalone scripts that verify the harness capabilities used here
```

`python3 -m pytest tests -q`

## AI assistance

Written with Claude Code (rule 11). The design decisions, the measurements behind
them, and the review of every line are the author's.

## License

MIT
