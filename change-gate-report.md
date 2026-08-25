## Change Gate report

**Verdict: no change outside noise.** 0 fixed, 0 broken, 0 flaky (unstable in at least one arm, not attributed to the change). Token cost 0.98× of baseline.

Every scenario ran 1× per arm, because the model is not deterministic: a scenario that passes once and fails once tells you nothing about the change.

| | baseline | candidate |
|---|---|---|
| passed | 14/16 (88%) | 14/16 (88%) |
| total tokens | 65,995 | 64,766 |

### Per scenario

| scenario | baseline | candidate | change | why |
|---|---|---|---|---|
| `issue-331072` | 1/1 | 1/1 | same |  |
| `issue-331125` | 1/1 | 1/1 | same |  |
| `issue-331272` | 1/1 | 1/1 | same |  |
| `issue-331291` | 0/1 | 0/1 | same | expected 'feature-request', got 'bug' |
| `issue-331350` | 1/1 | 1/1 | same |  |
| `issue-331367` | 1/1 | 1/1 | same |  |
| `issue-331368` | 1/1 | 1/1 | same |  |
| `issue-331374` | 1/1 | 1/1 | same |  |
| `issue-331980` | 1/1 | 1/1 | same |  |
| `issue-331988` | 1/1 | 1/1 | same |  |
| `issue-332051` | 1/1 | 1/1 | same |  |
| `issue-332070` | 1/1 | 1/1 | same |  |
| `issue-332082` | 0/1 | 0/1 | same | expected 'bug', got 'feature-request' |
| `issue-332115` | 1/1 | 1/1 | same |  |
| `issue-332124` | 1/1 | 1/1 | same |  |
| `issue-332167` | 1/1 | 1/1 | same |  |

### Measuring stick

- source: https://github.com/microsoft/vscode/issues (labels as ground truth)
- revision: issues fetched via GitHub API; ids: issue-331072,issue-331125,issue-331272,issue-331291,issue-331350,issue-331367,issue-
- digest: `378de14cfb270b4b9137dd39f69f1dac24725bd3382259e5a673d314f9fc9c71`

The digest is recomputed from the scenario contents on every run. Editing a prompt or adding a scenario changes it and the run is refused.
