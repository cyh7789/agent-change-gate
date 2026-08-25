## Change Gate report

**Verdict: no change outside noise.** 0 fixed, 0 broken, 1 flaky (unstable in at least one arm, not attributed to the change). Token cost 1.01× of baseline.

Every scenario ran 3× per arm, because the model is not deterministic: a scenario that passes once and fails once tells you nothing about the change.

| | baseline | candidate |
|---|---|---|
| passed | 43/48 (90%) | 42/48 (88%) |
| total tokens | 188,675 | 190,430 |

### Per scenario

| scenario | baseline | candidate | change | why |
|---|---|---|---|---|
| `issue-331072` | 3/3 | 3/3 | same |  |
| `issue-331125` | 3/3 | 3/3 | same |  |
| `issue-331272` | 3/3 | 3/3 | same |  |
| `issue-331291` | 0/3 | 0/3 | same | expected 'feature-request', got 'bug' |
| `issue-331350` | 3/3 | 3/3 | same |  |
| `issue-331367` | 3/3 | 3/3 | same |  |
| `issue-331368` | 3/3 | 3/3 | same |  |
| `issue-331374` | 3/3 | 3/3 | same |  |
| `issue-331980` | 3/3 | 3/3 | same |  |
| `issue-331988` | 3/3 | 3/3 | same |  |
| `issue-332051` | 3/3 | 3/3 | same |  |
| `issue-332070` | 3/3 | 3/3 | same |  |
| `issue-332082` | 1/3 | 0/3 | flaky | expected 'bug', got 'feature-request' |
| `issue-332115` | 3/3 | 3/3 | same |  |
| `issue-332124` | 3/3 | 3/3 | same |  |
| `issue-332167` | 3/3 | 3/3 | same |  |

### Statistical read

Across the 16 evaluation scenarios (48 total trials per arm), the baseline achieved 43 total passes (89.58% pass rate, 95% Wilson score interval: [0.7783, 0.9547]), while the candidate achieved 42 total passes (87.50% pass rate, 95% Wilson score interval: [0.7530, 0.9414]). The two 95% Wilson score intervals overlap significantly, indicating no statistically meaningful performance difference between the arms overall. Furthermore, there are zero scenarios where the baseline and candidate differ by more than one repeat—15 of the 16 scenarios produced identical pass counts (14 with 3/3 passes and 1 with 0/3 passes), with the only difference occurring in scenario `issue-332082` (1 pass for baseline vs. 0 passes for candidate).

| Arm | Total Passes / Total Trials | Pass Rate | Wilson 95% Score Interval |
| :--- | :---: | :---: | :---: |
| Baseline | 43 / 48 | 89.58% | [0.7783, 0.9547] |
| Candidate | 42 / 48 | 87.50% | [0.7530, 0.9414] |

_Computed by code the agent wrote and ran in its sandbox, from the pass counts above. The verdict itself is not: it comes from the deterministic checks, so the same outputs always score the same._

### Measuring stick

- source: https://github.com/microsoft/vscode/issues (labels as ground truth)
- revision: issues fetched via GitHub API; ids: issue-331072,issue-331125,issue-331272,issue-331291,issue-331350,issue-331367,issue-
- digest: `378de14cfb270b4b9137dd39f69f1dac24725bd3382259e5a673d314f9fc9c71`

The digest is recomputed from the scenario contents on every run. Editing a prompt or adding a scenario changes it and the run is refused.
