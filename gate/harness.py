"""HTTP interface to the TrueForge harness.

It wraps only the four things this project needs: create an agent, open a session, run a
turn, and resume an interrupted one. The turn endpoint answers with SSE, not JSON. Events
carry a monotonically increasing sequence id; on resume the last id seen goes back as an
exclusive cursor and the harness replays everything after it.
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
    """One subagent's whole life: what it was given, what it answered."""
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
        """Reconstruct each subagent of the fan-out.

        Mapping has to go through thread_id: thread.done arrives in a different order than
        thread.created. Measured on a four-item fan-out, created was 1234 and done was 2143,
        so matching by position attaches answers to the wrong items.
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
        """The turn has reached an end: finished, failed, or stopped at the approval gate."""
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
    except DROPPED as e:
        # Callers guard their "return None when it cannot be fetched" path with
        # `except HarnessError`. A dropped connection raises URLError, which skips that guard
        # entirely, and one tool-name lookup is then enough to take down the approval gate.
        raise HarnessError(f"{path}: {e}") from e


def create_agent(name: str, manifest: dict) -> str:
    return _request("/agents", {"name": name, "manifest": manifest})["data"]["id"]


def create_session(agent_name: str) -> str:
    return _request("/sessions", {"agent": {"name": agent_name}})["data"]["id"]


def _stream(url: str, body: dict | None) -> Iterator[Event]:
    """An SSE event ends at the blank line, and `id:` belongs to the `data:` above it.

    The harness sends data first and id second, so an event cannot be emitted the moment its
    data arrives: doing that pairs every event with the previous event's number, leaving the
    resume cursor one short and replaying an event already received.
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
        # HTTPError subclasses URLError. Without this conversion a 400 falls into the resume
        # path, gets retried as if the connection dropped, and quietly ends up reported as
        # "this batch produced no output".
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
        if payload is not None:                     # the stream did not end on a blank line
            yield Event(seq=seq, type=payload.get("type", ""), data=payload)


def _consume_into(out: TurnResult, events: Iterator[Event], stop_at_approval: bool) -> TurnResult:
    # The completion event is `turn.done`, not turn.completed. Event names here are measured
    # against the live stream, not guessed.
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
    """Run a turn, resuming from the break if the connection drops.

    The turn keeps running server-side, so a dropped connection should not restart the batch.
    A batch of scenarios takes minutes and re-running it costs the whole batch in tokens.
    Resume uses the sequence as an exclusive cursor, so no event repeats and none is lost.
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
            # Not even turn.created arrived, yet the turn is already running on the server.
            # Abandoning it burns that call for nothing.
            turn_id, after = _find_turn(session_id, message), 0
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
        # Returning empty-handed shows the caller only "no output", which gets recorded as the
        # model failing to answer.
        raise HarnessError(
            f"turn {out.turn_id} never reached a terminal event after {reconnects} reconnects"
            if out.turn_id else
            "the turn stream dropped before turn.created, so there is nothing to resume")
    return out


def _find_turn(session_id: str, message: str) -> str | None:
    """Find the turn we just sent on this session.

    It matches on the input the turn itself records. Taking the last entry picks the wrong
    turn whenever another one is running on the same session, and resumes something that is
    not ours. The endpoint lists oldest first, measured on three turns created in order, so
    the search runs backwards from the end.
    """
    try:
        turns = _request(f"/sessions/{session_id}/turns")["data"]
    except (HarnessError, KeyError):
        return None
    for turn in reversed(turns):
        for item in turn.get("input") or []:
            if item.get("content") == message:
                return turn["id"]
    return None


def describe_call(session_id: str, source_event_id: str, call_id: str) -> dict | None:
    """What the call at the gate actually does: `{"tool": "create_branch", "server": "github", "input": {...}}`.

    Measured: `tool.approval_required` carries only `{id, source_event_id}`, with no tool name
    in it. The name lives in the `function.arguments` of the `model.message` that
    `source_event_id` points at, which means going back to the session's event stream. When
    it cannot be fetched this returns None and the caller keeps its original string.
    """
    try:
        events = _request(f"/sessions/{session_id}/events")["data"]
    except (HarnessError, KeyError):
        return None
    for row in events:
        ev = row.get("event", row)
        if ev.get("id") != source_event_id:
            continue
        for call in ev.get("tool_calls") or []:
            if call.get("id") != call_id:
                continue
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                return {"tool": fn.get("name"), "server": None, "input": {}}
            # An MCP call is wrapped in call_tool, so the real tool name is inside arguments.
            return {"tool": args.get("tool_name") or fn.get("name"),
                    "server": args.get("mcp_server"),
                    # Long strings are file contents; on the card they crowd out what matters
                    "input": {k: v for k, v in (args.get("input") or {}).items()
                              if isinstance(v, (str, int, float)) and len(str(v)) <= 60}}
    return None


def _subscribe_url(session_id: str, turn_id: str, after_seq: int) -> str:
    return f"{BASE}/sessions/{session_id}/turns/{turn_id}/subscribe?after_sequence_number={after_seq}"


def resume_turn(session_id: str, turn_id: str, after_seq: int) -> TurnResult:
    """Resume from the break. after_seq is exclusive: only higher-numbered events are replayed."""
    return _consume(_stream(_subscribe_url(session_id, turn_id, after_seq), None),
                    stop_at_approval=True)


def decide(session_id: str, thread_id: str, tool_call_id: str, allow: bool,
           reason: str = "") -> TurnResult:
    """Answer one approval request.

    It stops at the next approval request: a write-back is usually several tool calls (branch,
    commit, pull request) and each needs its own decision. Reading to the end would block,
    because the turn is not over.
    """
    approval = {"status": "allow"} if allow else {"status": "deny", "reason": reason}
    body = {"input": [{"type": "user.tool_approval", "thread_id": thread_id,
                       "tool_call_id": tool_call_id, "approval": approval}]}
    return _consume(_stream(f"{BASE}/sessions/{session_id}/turns", body), stop_at_approval=True)
