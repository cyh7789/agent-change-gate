#!/usr/bin/env python3
"""Freeze a scenario set from the issues of a public repository.

Why someone else's repo: the gate measures whether a change makes the agent better or worse,
and a measuring stick written by the same person can quietly favour the candidate. GitHub
issues come with ground truth the maintainers applied themselves, and anyone can pull the
same batch through the same API and re-run it.

Only issues carrying a single primary-category label are taken. When an issue is both bug and
ui, the "right answer" is itself ambiguous and the item measures nothing.

Usage: python3 scripts/freeze_scenarios.py owner/repo out.json [count]
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gate.scenarios import freeze

# The categories have to be mutually exclusive and actually used by maintainers. Sampling per
# category through the search API replaces scanning every issue: a small repo has too few
# single-label issues (all 32 issues in trueforge yielded only 8 items), and a skewed
# distribution lets an agent that always guesses the majority category score well.
PRIMARY = ("bug", "feature-request", "documentation")


def main() -> None:
    repo = sys.argv[1] if len(sys.argv) > 1 else "truefoundry/trueforge"
    out = sys.argv[2] if len(sys.argv) > 2 else "scenarios/issue-triage.json"
    want = int(sys.argv[3]) if len(sys.argv) > 3 else 20

    per_class = max(1, want // len(PRIMARY))
    picked = []
    for label in PRIMARY:
        raw = subprocess.check_output([
            "gh", "api",
            f"search/issues?q=repo:{repo}+label:{label}+state:closed+is:issue&per_page={per_class * 3}",
            "-q", '.items[] | {number, title, body, labels: [.labels[].name]}',
        ], text=True)
        got = 0
        for line in raw.splitlines():
            if not line.strip():
                continue
            it = json.loads(line)
            # Drop anything carrying more than one primary category: its "right answer" is ambiguous
            if len([l for l in it["labels"] if l in PRIMARY]) != 1:
                continue
            body = (it.get("body") or "").strip()
            if len(body) < 80:      # too little text to classify
                continue
            picked.append({
                "id": f"issue-{it['number']}",
                "prompt": (
                    "Classify this GitHub issue into exactly one category: "
                    + ", ".join(PRIMARY) + ".\n"
                    "Answer with the single category and nothing else.\n\n"
                    f"Title: {it['title']}\n\nBody:\n{body[:1500]}"
                ),
                "expect": {"equals_ignoring_case": label},
            })
            got += 1
            if got >= per_class:
                break

    picked.sort(key=lambda s: s["id"])
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    digest = freeze(
        source=f"https://github.com/{repo}/issues (labels as ground truth)",
        revision=f"issues fetched via GitHub API; ids: {','.join(s['id'] for s in picked)}",
        scenarios=picked, out=out)
    dist = {}
    for s in picked:
        k = s["expect"]["equals_ignoring_case"]
        dist[k] = dist.get(k, 0) + 1
    print(f"{len(picked)} scenarios → {out}")
    print(f"digest {digest[:16]}…")
    print("label distribution:", json.dumps(dist))


if __name__ == "__main__":
    main()
