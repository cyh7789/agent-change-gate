#!/usr/bin/env python3
"""從一個公開 repo 的 issue 凍結一組情境。

為什麼是別人的 repo：閘門要量的是「這個變更讓 agent 變好還是變壞」，
量尺如果是自己寫的，寫的人就能不自覺地寫出對候選有利的題目。
GitHub issue 有現成的 ground truth，維護者自己貼的 label，而且任何人
都能用同一個 API 抓同一批來重跑。

只收有單一主類別 label 的 issue：一個 issue 同時是 bug 又是 ui 時，
「正確答案」本身有歧義，那種題目量不出東西。

用法：python3 scripts/freeze_scenarios.py owner/repo out.json [數量]
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gate.scenarios import freeze

# 類別要互斥且維護者實際在用。用 search API 逐類取樣，取代掃全部 issue：
# 小 repo 帶單一主 label 的 issue 太少（trueforge 全部 32 個 issue 只湊到 8 題），
# 而類別分布一偏，只會猜多數類別的 agent 就能拿高分。
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
            # 排除同時掛多個主類別的：那種題目的「正確答案」本身有歧義
            if len([l for l in it["labels"] if l in PRIMARY]) != 1:
                continue
            body = (it.get("body") or "").strip()
            if len(body) < 80:      # 內容太短，分類不出東西
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
