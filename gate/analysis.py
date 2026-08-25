"""通過次數矩陣的統計解讀：agent 寫程式，程式在 sandbox 裡跑。

這不是 TrueForge 講的 Code Mode。那個指的是 sandbox 裡的程式透過 `mcp_client`
呼叫 MCP tools，把中間結果留在 sandbox；這裡的次數矩陣本來就在記憶體裡，
繞一個 tool 什麼也換不到。

為什麼不是自己寫死一段統計：這一段要回答的問題會變。今天問兩組的區間有沒有
重疊，下次可能問某一題是不是本來就不穩、或哪幾題撐起了整個差異。讓 agent 針對
手上的矩陣現寫程式，比預先窮舉所有問法實際。

界線畫在判決上。fixed / broken / flaky 與 verdict 全部由 `checks.py` 和
`report.py` 的確定性程式算完才進來這裡；sandbox 只拿到已經定案的次數矩陣，
產出的是解讀，不會反過來改判決。跑不起來就沒有這一段，報表照出。
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
    """每組帶自己的次數：某一組有幾次沒跑成的時候，共用一個 n 會讓 sandbox 算錯。"""
    return json.dumps([{"id": r["id"],
                        "baseline": r["baseline_pass"], "baseline_n": r["baseline_n"],
                        "candidate": r["candidate_pass"], "candidate_n": r["candidate_n"]}
                       for r in rows], indent=1)


def interpret(rows: list[dict], model: str = "google-gemini/gemini-3-6-flash") -> str | None:
    """回傳 markdown 區塊；sandbox 或模型出問題時回 None。"""
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
        # 評測已經跑了幾分鐘。附加的解讀壞掉不該把那些結果一起丟掉。
        return None
    if not res.output:
        return None
    started = any(e.type == "sandbox.created" for e in res.events)
    executed = any(e.type == "tool.response" for e in res.events)
    if not (started and executed):
        return None          # 只有對話沒有工具回應，代表數字是講出來的，不是跑出來的
    return res.output.strip()
