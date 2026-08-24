## Change Gate report

**Verdict: no change.** 14/16 passed, baseline 14/16. 0 fixed, 0 broken. Token cost 1.06× of baseline.

| | baseline | candidate |
|---|---|---|
| passed | 14/16 (88%) | 14/16 (88%) |
| total tokens | 28,136 | 29,804 |

### Per scenario

| scenario | baseline | candidate | change | why |
|---|---|---|---|---|
| `issue-331072` | pass | pass | same |  |
| `issue-331125` | pass | pass | same |  |
| `issue-331272` | pass | pass | same |  |
| `issue-331291` | fail | fail | same | expected 'feature-request', got 'bug' |
| `issue-331350` | pass | pass | same |  |
| `issue-331367` | pass | pass | same |  |
| `issue-331368` | pass | pass | same |  |
| `issue-331374` | pass | pass | same |  |
| `issue-331980` | pass | pass | same |  |
| `issue-331988` | pass | pass | same |  |
| `issue-332051` | pass | pass | same |  |
| `issue-332070` | pass | pass | same |  |
| `issue-332082` | fail | fail | same | expected 'bug', got 'feature-request' |
| `issue-332115` | pass | pass | same |  |
| `issue-332124` | pass | pass | same |  |
| `issue-332167` | pass | pass | same |  |

### Measuring stick

- source: https://github.com/microsoft/vscode/issues (labels as ground truth)
- revision: issues fetched via GitHub API; ids: issue-331072,issue-331125,issue-331272,issue-331291,issue-331350,issue-331367,issue-
- digest: `378de14cfb270b4b9137dd39f69f1dac24725bd3382259e5a673d314f9fc9c71`

The digest is recomputed from the scenario contents on every run. Editing a prompt or adding a scenario changes it and the run is refused.
