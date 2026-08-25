"""TrueForge harness 的 HTTP 介面。

只包住我們用得到的四件事：建 agent、開 session、跑 turn、續接被中斷的 turn。
turn 端點回的是 SSE，不是 JSON。事件逐筆帶單調遞增的 sequence id，
續接時把最後看到的 id 當 exclusive cursor 傳回去，harness 會從那之後重放。
"""
from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterator

BASE = os.environ.get("TRUEFORGE_BASE", "http://localhost:8790/api/v1")


class HarnessError(RuntimeError):
    pass


@dataclass
class Event:
    seq: int | None
    type: str
    data: dict


@dataclass
class ThreadResult:
    """一個 subagent 的一生：收到什麼、回了什麼。"""
    thread_id: str
    input: str
    output: str | None = None
    status: str | None = None


@dataclass
class TurnResult:
    events: list[Event] = field(default_factory=list)
    output: str | None = None
    metrics: dict = field(default_factory=dict)
    pending_approval: dict | None = None

    @property
    def threads(self) -> list[ThreadResult]:
        """把扇出的 subagent 逐一還原。

        對映一定要走 thread_id：thread.done 的到達順序跟 thread.created 不同
        （實測四題扇出，created 是 1234，done 是 2143），照順序配會把答案錯位。
        """
        seen: dict[str, ThreadResult] = {}
        for ev in self.events:
            if ev.type == "thread.created":
                tid = ev.data.get("thread_id")
                info = ev.data.get("agent_info") or {}
                if tid:
                    seen[tid] = ThreadResult(tid, info.get("input") or "")
            elif ev.type == "thread.done":
                tid = ev.data.get("thread_id")
                if tid not in seen:
                    continue
                state = ev.data.get("state") or {}
                seen[tid].status = state.get("status")
                content = ((state.get("output") or {}).get("content"))
                if isinstance(content, list):
                    content = "".join(part.get("text", "") for part in content
                                      if isinstance(part, dict))
                seen[tid].output = content
        return list(seen.values())

    @property
    def turn_id(self) -> str | None:
        for e in self.events:
            if e.type == "turn.created":
                tid = e.data.get("turn_id") or (e.data.get("state") or {}).get("id")
                if tid:
                    return tid
        return None

    @property
    def finished(self) -> bool:
        """這個 turn 已經有結局：跑完、失敗，或停在核准閘。"""
        return self.pending_approval is not None or any(
            e.type in ("turn.done", "turn.failed") for e in self.events)

    @property
    def last_seq(self) -> int | None:
        for e in reversed(self.events):
            if e.seq is not None:
                return e.seq
        return None


def _request(path: str, body: dict | None = None, method: str | None = None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise HarnessError(f"{e.code} {path}: {e.read().decode()[:300]}") from e


def create_agent(name: str, manifest: dict) -> str:
    return _request("/agents", {"name": name, "manifest": manifest})["data"]["id"]


def create_session(agent_name: str) -> str:
    return _request("/sessions", {"agent": {"name": agent_name}})["data"]["id"]


def _stream(url: str, body: dict | None) -> Iterator[Event]:
    """一個 SSE 事件在空行處結束，`id:` 跟它前面的 `data:` 屬於同一個事件。

    harness 送的順序是 data 先、id 後，所以事件不能一看到 data 就吐出去：
    那樣每個事件都會配到前一個的編號，續接游標少一格，重放一個已經收過的事件。
    """
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    seq: int | None = None
    payload: dict | None = None
    try:
        r = urllib.request.urlopen(req, timeout=1800)
    except urllib.error.HTTPError as e:
        # HTTPError 是 URLError 的子類。不在這裡轉成 HarnessError 的話，一個 400
        # 會落進續接那條路，被當成斷線重試，最後安靜地變成「這批沒有輸出」。
        raise HarnessError(f"{e.code} {url}: {e.read().decode()[:300]}") from e
    with r:
        for raw in r:
            line = raw.decode(errors="replace").strip()
            if line.startswith("id:"):
                seq = int(line[3:].strip())
            elif line.startswith("data:"):
                payload = json.loads(line[5:].strip())
            elif line == "":
                if payload is not None:
                    yield Event(seq=seq, type=payload.get("type", ""), data=payload)
                seq, payload = None, None
        if payload is not None:                     # 串流沒有以空行收尾
            yield Event(seq=seq, type=payload.get("type", ""), data=payload)


def _consume_into(out: TurnResult, events: Iterator[Event], stop_at_approval: bool) -> TurnResult:
    # 完成事件是 `turn.done`（不是 turn.completed）。實測 SSE 事件名，別照猜的寫。
    for ev in events:
        out.events.append(ev)
        if ev.type == "tool.approval_required" and stop_at_approval:
            out.pending_approval = ev.data
            break
        if ev.type in ("turn.done", "turn.failed"):
            state = ev.data.get("state") or {}
            out.output = ((state.get("output") or {}).get("content")) or None
            out.metrics = state.get("metrics") or {}
            break
    return out


def _consume(events: Iterator[Event], stop_at_approval: bool) -> TurnResult:
    return _consume_into(TurnResult(), events, stop_at_approval)


DROPPED = (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError)


def run_turn(session_id: str, message: str, stop_at_approval: bool = True,
             reconnects: int = 3) -> TurnResult:
    """跑一個 turn，連線掉了就從斷點續接。

    turn 在伺服器那端繼續跑，所以斷線不該讓整批重來。一批情境要跑好幾分鐘，
    重跑的代價是整批的 token。續接用 sequence 當 exclusive cursor，事件不重複也不遺漏。
    """
    body = {"input": [{"type": "user.message", "content": message}]}
    out = TurnResult()
    try:
        _consume_into(out, _stream(f"{BASE}/sessions/{session_id}/turns", body), stop_at_approval)
    except DROPPED:
        pass
    for _ in range(reconnects):
        if out.finished:
            break
        turn_id, after = out.turn_id, out.last_seq
        if turn_id is None:
            # 連 turn.created 都沒收到，但 turn 已經在伺服器上跑了。丟掉它等於
            # 白燒那次呼叫，而這個 session 最新的那一筆就是它。
            turn_id, after = _latest_turn(session_id), 0
            if turn_id is None:
                break
        if after is None:
            after = 0
        try:
            _consume_into(out, _stream(_subscribe_url(session_id, turn_id, after), None),
                          stop_at_approval)
        except DROPPED:
            continue
    if not out.finished:
        # 空手回去的話，呼叫端只看得到「沒有輸出」，那會被寫成模型答不出來。
        raise HarnessError(
            f"turn {out.turn_id} never reached a terminal event after {reconnects} reconnects"
            if out.turn_id else
            "the turn stream dropped before turn.created, so there is nothing to resume")
    return out


def _latest_turn(session_id: str) -> str | None:
    """這個 session 最新的一個 turn。

    端點回的是最舊在前（實測三個 turn，建立順序就是列出順序），所以要最後一筆。
    一頁最多 25 筆，而這裡的 session 一輩子只跑幾個 turn。
    """
    try:
        turns = _request(f"/sessions/{session_id}/turns")["data"]
    except (HarnessError, KeyError):
        return None
    return turns[-1]["id"] if turns else None


def _subscribe_url(session_id: str, turn_id: str, after_seq: int) -> str:
    return f"{BASE}/sessions/{session_id}/turns/{turn_id}/subscribe?after_sequence_number={after_seq}"


def resume_turn(session_id: str, turn_id: str, after_seq: int) -> TurnResult:
    """從斷點續接。after_seq 是 exclusive：只重放編號更大的事件。"""
    return _consume(_stream(_subscribe_url(session_id, turn_id, after_seq), None),
                    stop_at_approval=True)


def decide(session_id: str, thread_id: str, tool_call_id: str, allow: bool,
           reason: str = "") -> TurnResult:
    """回覆一次核准請求。

    停在下一次核准請求上：一個寫回動作通常是好幾個工具呼叫（開分支、提交檔案、
    開 PR），每一個都要各自的核准。讀到底的話會卡在等待，因為 turn 還沒結束。
    """
    approval = {"status": "allow"} if allow else {"status": "deny", "reason": reason}
    body = {"input": [{"type": "user.tool_approval", "thread_id": thread_id,
                       "tool_call_id": tool_call_id, "approval": approval}]}
    return _consume(_stream(f"{BASE}/sessions/{session_id}/turns", body), stop_at_approval=True)
