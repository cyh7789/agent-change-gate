"""Write an approved change back to GitHub.

The write-back deliberately goes through an agent holding the GitHub MCP server rather
than a direct REST call: the harness hangs its approval gate on tool calls, so bypassing
the tool bypasses the gate. That pause is the point. A person reads the comparison table
before the change is allowed to land.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

from . import harness

# Branch, file, pull request is the whole job. The GitHub MCP server exposes 44 tools and
# the `@all` default would hand this agent every one of them. The approval gate stops the
# writes, but it cannot stop the agent seeing tools it had no business seeing.
# Approval is the second boundary, not the first.
WRITEBACK_TOOLS = ["create_branch", "create_or_update_file", "push_files",
                   "create_pull_request"]

WRITEBACK_AGENT = {
    "model": {"name": "google-gemini/gemini-3-1-pro-preview"},
    "instructions": (
        "You land approved agent-spec changes on GitHub. Use the github tools. "
        "Create a branch, commit the new spec file, then open a pull request whose body is "
        "exactly the report you are given. Do not edit the report. Do not merge anything."
    ),
    # require_approval_for_tools is deliberately unset: TrueForge ships with
    # ["@write", "@destructive"], and the GitHub MCP server is what annotates these calls as
    # writes. What stops them is that default, not anything in this manifest.
    "mcp_servers": [{"name": "github", "enable_tools": WRITEBACK_TOOLS}],
    "config": {"iteration_limit": 25},
}


@dataclass
class PendingWrite:
    session_id: str
    thread_id: str
    tool_call_id: str
    call: dict          # {"tool", "server", "input"}; tool is None when the name cannot be resolved

    @property
    def tool_summary(self) -> str:
        """A one-line form for callers that cannot take the structure (CLI, logs)."""
        head = ".".join(p for p in (self.call.get("server"), self.call.get("tool")) if p)
        detail = ", ".join(f"{k}={v}" for k, v in (self.call.get("input") or {}).items())
        return f"{head}  {detail}".strip() or self.tool_call_id


def _pending(session_id: str, event: dict) -> PendingWrite:
    call = (event.get("tool_calls") or [{}])[0]
    # The gate has to show what is being approved. The event carries only a call id, so the
    # tool name has to be looked up.
    described = harness.describe_call(session_id, call.get("source_event_id", ""),
                                      call.get("id", ""))
    return PendingWrite(session_id=session_id, thread_id=event.get("thread_id", "main"),
                        tool_call_id=call.get("id", ""),
                        call=described or {"tool": None, "server": None, "input": {}})


def propose(repo: str, branch: str, path: str, content: str, title: str, report_md: str) -> PendingWrite | None:
    """Send the write-back and return the moment it stops at the gate. None means the agent triggered no tool needing approval."""
    # The random suffix matters: a fixed name that collides with an existing agent adopts
    # that agent's manifest, whose require_approval_for_tools may have been changed by
    # someone else. The gate would be bypassed with nothing to show for it.
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
    """Run the write-back to the end, asking once for every tool call that needs approval.

    Branch, commit and pull request are three tool calls and the harness stops at each one.
    Answering only the first leaves the turn parked on the second, and GitHub holding a
    branch with no pull request.

    Returns (everything approved, the agent's final output). Any rejection ends it.
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
