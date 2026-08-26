# Agent Change Gate

Changing an agent's prompt is a deploy. This is the thing that stands between the
change and production: it re-runs a frozen scenario set against both the current
spec and the candidate, tells the difference between a real regression and model
noise, and then stops so a human decides whether the change lands on GitHub.

Built on the TrueForge harness for the WeMakeDevs TrueForge Hackathon.

```
real tools through MCP   →  the write-back is GitHub MCP, never the REST API
code run in a sandbox    →  the agent writes and executes the statistical read
                            (not Code Mode: no MCP calls from inside the sandbox)
work handed to subagents →  one subagent per scenario, no concurrency in this code
a human before anything  →  branch, commit and pull request each stop for a decision,
  reaches GitHub               under TrueForge's default policy, not one I wrote
a session that survives  →  a dropped turn resumes from its last sequence id
  a reconnect
```

Every one of those is re-runnable from `probe/`, against a live harness, in under a
minute. None of it is a claim you have to take from the video.

## The problem it solves

Someone rewrites a line of an agent's instructions and ships it. Nothing is red,
because nothing was measuring. The failure shows up a week later as "the bot has
been mislabelling things", and by then nobody can say which edit did it.

The obvious answer, run both versions and compare, breaks on first contact:

- **The model is not deterministic.** Running the same spec against the same 16
  scenarios three times gave 15/16, 14/16, 14/16, and one scenario (`issue-332082`)
  answered pass, fail, fail. Compare single runs and you will report noise as a
  regression. Compare three and you can still get it wrong, which is why the default
  is five; the measurements are below.
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
| **Code run in a sandbox** | The statistical read of the results (Wilson intervals, per-scenario stability) is **code the agent writes and runs in its sandbox**. The verdict is not: that stays in deterministic Python, so the same outputs always score the same. This is not TrueForge's Code Mode, which is generated Python calling MCP tools through an in-sandbox `mcp_client`; the counts are already in memory and routing them through a tool would buy nothing. |
| **Real tools via MCP** | Landing the change goes through the GitHub MCP server: branch, commit, pull request. No REST calls behind the harness's back. The write-back agent sees only the four tools that job needs, out of the 44 GitHub exposes: approval is the second boundary, not the first. |
| **Human approval** | Landing a change is three separate tool calls, and each one comes back for its own decision. The policy that stops them is not mine: `require_approval_for_tools: ["@write", "@destructive"]` is TrueForge's factory default, and GitHub's MCP server is what marks these calls as writes. I did not build the gate, I declined to disable it. |
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
implicit, the kind of edit nobody would think to test. Five runs per scenario,
16 scenarios, both specs. The whole report is
[PR #23](https://github.com/cyh7789/agent-change-gate/pull/23), opened by the gate
itself, and the video shows this run being made:

```
Verdict: no change outside noise. 0 fixed, 0 broken, 1 flaky, 0 unproven, 0 incomplete.
Token cost 1.02x of baseline.

passed        baseline 71/80 (89%)    candidate 74/80 (92%)
tokens        baseline 312,661        candidate 317,791
issue-332082  baseline 1/5            candidate 4/5            flaky
```

Read it as a decision rather than a scoreboard: the only scenario that moved at all
is one neither spec answers consistently, and the change costs more rather than less.
How much more is itself unstable — 1.02x here, 1.08x and 1.09x on two other five-repeat
runs of the same two specs. What holds across all of them is the direction and the
verdict; the single-run report claimed the opposite of both.

That one scenario is also the argument for running each spec more than once, and
the repository has both readings side by side. [PR #8](https://github.com/cyh7789/agent-change-gate/pull/8)
was opened by the gate itself from a one-run-per-arm evaluation, and its body says:

```
Verdict: improvement. 1 fixed, 0 broken, 0 flaky. Token cost 0.94x of baseline.
```

Same two specs, same 16 scenarios. One run per arm: an improvement that also saves
6% tokens. Five runs per arm: a coin flip that costs more, not less. The single run
got both the direction and the sign of the cost wrong, and nothing about that report
looks uncertain.

## Why the default is five, not three

Three used to be the default. The same two specs, evaluated four times at three runs
per arm, did not reach the same verdict:

| run | baseline | candidate | `issue-332082` | verdict | tokens |
|---|---|---|---|---|---|
| 1 | 43/48 | 42/48 | 1/3 → 0/3 | no change outside noise | 1.01x |
| 2 | 42/48 | 43/48 | 0/3 → 1/3 | no change outside noise | 1.07x |
| 3 | 44/48 | 45/48 | 2/3 → 3/3 | no change outside noise | 1.03x |
| 4 | 42/48 | 45/48 | **0/3 → 3/3** | **improvement** | 1.10x |

(Run 1 predates the fix in #5 and was comparing two specs that were never in effect; it is
here for completeness, not as evidence.)

One scenario drives all of it, and the fourth run exposes a real limitation in how `flaky`
is decided. The rule is "unstable in either arm", implemented as a partial pass:
`0 < passes < n`. When a scenario happens to land 0/3 in one arm and 3/3 in the other,
neither arm is partial, so nothing looks unstable and the change is credited with fixing it.

Three runs is enough to catch the direction error a single run makes, and not enough to settle a
scenario this noisy: the strongest split three repeats can produce, 0/3 against 3/3, is
p = 0.10 under a two-tailed Fisher exact test, so no single scenario can ever clear p ≤ 0.05.
That is why the default moved to five and why a scenario that moves without clearing the test
is reported as `unproven` rather than fixed. Counting passes is still the wrong instrument for
the last step; comparing intervals is the right one, and the sandbox read already does it:

```
Wilson intervals overlap: True
→ the increase from baseline to candidate is not statistically significant at 95%
```

So on the run that the deterministic verdict called an improvement, the statistical read
called it noise. The two layers have different blind spots, which is an argument for keeping
both rather than for trusting either alone. Folding interval overlap into the verdict itself
is the obvious next change, and it is deliberately not in this submission: the demo video
records the current behaviour, and shipping a different rule than the one on camera would be
worse than the limitation.

## Setup

Needs Python 3.11+, Node (for `npx`), the `gh` CLI logged in, and a Gemini API key.
Tested against TrueForge 0.1.4; this client depends on the HTTP contract, the SSE
event names, the resume cursor and the turn ordering, so the version is pinned.

```bash
# 1. run the harness (keeps its config in ~/Library/Application Support/trueforge)
npx -y @truefoundry/trueforge@0.1.4    # serves http://localhost:8790

# 2. point it at a model provider and the GitHub MCP server
GEMINI_API_KEY=... ./scripts/setup_harness.sh
#    -> model provider: ok / github mcp: ok / auth status: github=authenticated

# 3. check the pieces this project relies on
#    the gate itself is standard library only; pytest is the one test-time dependency
uvx --python 3.11 --from pytest pytest -q tests   # or: pip install pytest && python3 -m pytest -q tests
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

Flags: `--repeat` (runs per scenario per arm, default 5, because one run cannot separate
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

Each run mints a token and hands it to whoever loads the page; `/decide` refuses
approvals without it. Binding to localhost only keeps other machines out, and the
thing behind that endpoint is a write to someone else's GitHub repository.

## The scenario set

16 issues from `microsoft/vscode`, labelled by the maintainers, split 8 bug /
8 feature-request. `scripts/freeze_scenarios.py` builds it; `gate/scenarios.py`
verifies the digest on load and refuses to run against an edited set.

Scoring is deterministic string checking (`gate/checks.py`), never a model judging
a model: a grader that disagrees with itself between runs makes the comparison
worthless.

## What this has and has not been shown to do

Everything measured here is one agent doing one job: classifying GitHub issues into
two labels, checked by string equality. That is the whole evidence base, and it is
worth being precise about which parts of the gate depend on it.

Independent of the task: the fan-out to subagents, the incomplete/flaky/unproven
accounting, the Fisher exact test, the sandbox read, the approval gate, the resume
cursor, and the digest that refuses an edited scenario set. Those are harness and
statistics, and a scenario is a prompt plus an expectation to them.

Dependent on the task: `gate/checks.py` supports `equals_ignoring_case` and
`contains`, which covers short classification answers and nothing else. An agent
that writes a paragraph, calls a tool, or returns JSON needs a checker this
repository does not have, and swapping in a model as the grader is the one thing
that would undo the point — a grader that disagrees with itself between runs makes
every number above meaningless. Adding a deterministic checker per output shape is
the honest way forward, and none of them is written.

So the claim is: a spec change gets measured before it ships, on any task whose
output a deterministic check can score. Two labels and sixteen issues is where that
has actually been demonstrated.

## Layout

```
gate/harness.py     TrueForge HTTP + SSE client, resume cursor, approval decisions
gate/fanout.py      hands a batch of scenarios to subagents, maps answers back
gate/runner.py      runs one arm over the frozen set
gate/checks.py      deterministic pass/fail
gate/report.py      comparison, flaky classification, verdict, markdown
gate/analysis.py    statistical read, written and run in the agent's sandbox
gate/writeback.py   GitHub MCP write-back behind the approval gate
gate/state.py       the run's live state, and the approval the console holds
gate/web.py         the console: progress, table, approve/reject
probe/              standalone scripts that verify the harness capabilities used here
```

`uvx --python 3.11 --from pytest pytest -q tests`

## Qodo code review evidence

Qodo raised **28 findings** across #1, #3, #5, #6, #8, #9, #10, #11 and #13 — every pull
request it reviewed. All of them were read; the per-finding disposition, including the two
judged not to be defects and why, is the comment on
[#10](https://github.com/cyh7789/agent-change-gate/pull/10), and every finding also carries
its own reply on the thread it was raised in. From #14 on, Qodo's reviews are paused on this
account, so #14 through #24 carry its "reviews are paused" notice instead of a review. Those
pull requests are the video, the console styling and the documentation re-seal; the gate's
own code was reviewed.

Four were security issues, and all four were real:

| finding | what it meant |
|---|---|
| Credentials leak through argv | `ps` showed the Gemini API key to every user on the machine |
| Unauthenticated approval endpoint | binding to localhost keeps other machines out, not other processes, and that endpoint releases writes to GitHub |
| Unescaped HTML injection | tool summaries, failure reasons and the sandbox's own analysis are model-written and went into `innerHTML` |
| Writeback agent reuse risk | a fixed agent name silently adopted an existing agent, inheriting whatever `require_approval_for_tools` it had been created with — the one setting the whole gate rests on |

The one worth reading is the approval deadlock. `ask()` published the pending call, released
the lock, then cleared the event, so a decision landing in that window was erased and the
evaluation thread waited forever. In a demo that is a console frozen on "awaiting approval"
with no way to unfreeze it. It is closed structurally, under one `Condition`, and **no test
pins it**: the window is a few instructions wide and 200 paired rounds never reproduced it
against the old code. A test that passes on both versions would be worse than the admission.

Qodo also flagged "subagents recursively fan out" early, from a premise that turned out to be
wrong. Subagents inherit nothing at all, which is worse, and reading that review sooner would
have found it hours earlier.

Two of the fixes came with their own mistake, which is the honest part of the trail:
recovering an abandoned turn first took `data[0]` from the turn listing, and that listing is
oldest-first. The test written alongside it had a single-element list, so it could not have
caught the error. Qodo's follow-up then pointed out that even the newest turn is the wrong
answer, because nothing tied it to the turn being recovered; it now matches on the input the
turn records.

## AI assistance

Written with Claude Code (rule 11). The design decisions, the measurements behind
them, and the review of every line are the author's.

## License

MIT
