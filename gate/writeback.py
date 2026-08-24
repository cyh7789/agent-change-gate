"""核准後把變更寫回 GitHub。

寫回這一步刻意交給 agent 帶 GitHub MCP 去做，而不是自己呼叫 REST API：
harness 的核准閘是掛在工具呼叫上的，繞過工具就繞過了閘門。這裡要的正是
那個暫停 —— 人看完比較表才決定要不要讓這個變更落地。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import harness

WRITEBACK_AGENT = {
    "model": {"name": "google-gemini/gemini-3-1-pro-preview"},
    "instructions": (
        "You land approved agent-spec changes on GitHub. Use the github tools. "
        "Create a branch, commit the new spec file, then open a pull request whose body is "
        "exactly the report you are given. Do not edit the report. Do not merge anything."
    ),
    "mcp_servers": [{"name": "github"}],   # require_approval_for_tools 預設 @write/@destructive
    "config": {"iteration_limit": 25},
}


@dataclass
class PendingWrite:
    session_id: str
    thread_id: str
    tool_call_id: str
    tool_summary: str


def propose(repo: str, branch: str, path: str, content: str, title: str, report_md: str) -> PendingWrite | None:
    """送出寫回請求，回傳停在核准閘的那一刻。None 代表 agent 沒有觸發任何需要核准的工具。"""
    name = f"writeback-{branch.replace('/', '-')}"
    try:
        harness.create_agent(name, WRITEBACK_AGENT)
    except harness.HarnessError:
        pass                      # 同名已存在就沿用
    sid = harness.create_session(name)
    msg = (
        f"Repository: {repo}\n"
        f"Create branch `{branch}` from the default branch, write this file, and open a pull request.\n\n"
        f"File path: {path}\n"
        f"File content:\n```json\n{content}\n```\n\n"
        f"Pull request title: {title}\n"
        f"Pull request body (use verbatim):\n{report_md}\n"
    )
    res = harness.run_turn(sid, msg, stop_at_approval=True)
    if res.pending_approval is None:
        return None
    ev = res.pending_approval
    call = (ev.get("tool_calls") or [{}])[0]
    return PendingWrite(session_id=sid, thread_id=ev.get("thread_id", "main"),
                        tool_call_id=call.get("id", ""), tool_summary=str(call)[:200])


def approve(pending: PendingWrite) -> str | None:
    return harness.decide(pending.session_id, pending.thread_id, pending.tool_call_id, allow=True).output


def reject(pending: PendingWrite, reason: str) -> str | None:
    return harness.decide(pending.session_id, pending.thread_id, pending.tool_call_id,
                          allow=False, reason=reason).output
