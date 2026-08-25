"""核准後把變更寫回 GitHub。

寫回這一步刻意交給 agent 帶 GitHub MCP 去做，而不是自己呼叫 REST API：
harness 的核准閘是掛在工具呼叫上的，繞過工具就繞過了閘門。這裡要的正是
那個暫停。人看完比較表才決定要不要讓這個變更落地。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

from . import harness

# 開分支、寫檔、開 PR 就是全部。GitHub MCP 給的是 44 個工具，預設 `@all` 會讓這個
# agent 看到全部；核准閘擋得住寫入，但擋不住「它本來就不該看見那些工具」。
# 核准是第二道界線，不是第一道。
WRITEBACK_TOOLS = ["create_branch", "create_or_update_file", "push_files",
                   "create_pull_request"]

WRITEBACK_AGENT = {
    "model": {"name": "google-gemini/gemini-3-1-pro-preview"},
    "instructions": (
        "You land approved agent-spec changes on GitHub. Use the github tools. "
        "Create a branch, commit the new spec file, then open a pull request whose body is "
        "exactly the report you are given. Do not edit the report. Do not merge anything."
    ),
    # require_approval_for_tools 刻意不設：TrueForge 出廠就是 ["@write", "@destructive"]，
    # 而 GitHub MCP 自己把這些呼叫標成 write。攔下它們的不是這份設定，是那個預設。
    "mcp_servers": [{"name": "github", "enable_tools": WRITEBACK_TOOLS}],
    "config": {"iteration_limit": 25},
}


@dataclass
class PendingWrite:
    session_id: str
    thread_id: str
    tool_call_id: str
    tool_summary: str


def _pending(session_id: str, event: dict) -> PendingWrite:
    call = (event.get("tool_calls") or [{}])[0]
    return PendingWrite(session_id=session_id, thread_id=event.get("thread_id", "main"),
                        tool_call_id=call.get("id", ""), tool_summary=str(call)[:200])


def propose(repo: str, branch: str, path: str, content: str, title: str, report_md: str) -> PendingWrite | None:
    """送出寫回請求，回傳停在核准閘的那一刻。None 代表 agent 沒有觸發任何需要核准的工具。"""
    # 名字帶亂數：固定名字碰上既有的同名 agent 會沿用它的設定，而那份設定的
    # require_approval_for_tools 可能是別人調過的，核准閘就這樣被繞過去。
    name = f"writeback-{uuid.uuid4().hex[:8]}"
    harness.create_agent(name, WRITEBACK_AGENT)
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
    return _pending(sid, res.pending_approval)


def land(pending: PendingWrite, ask: Callable[[PendingWrite], bool],
         reason: str = "Rejected at the change gate.") -> tuple[bool, str | None]:
    """把寫回跑完，每一個要核准的工具都問過一次。

    開分支、提交、開 PR 是三個工具呼叫，harness 會逐一停下來要核准；只回答第一個
    的話，turn 會停在第二個上，GitHub 上留下一個沒有 PR 的分支。

    回傳 (是否全部核准, agent 的最後輸出)。任何一次拒絕就結束。
    """
    current = pending
    while True:
        allow = ask(current)
        res = harness.decide(current.session_id, current.thread_id,
                             current.tool_call_id, allow=allow, reason=reason)
        if not allow:
            return False, res.output
        if res.pending_approval is None:
            return True, res.output
        current = _pending(current.session_id, res.pending_approval)
