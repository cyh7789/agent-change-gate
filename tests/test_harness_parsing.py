"""Behaviour tests for event-stream parsing.

No real harness is involved: SSE parsing and the stop conditions are pure logic, checked
against a fabricated event stream. These bite on three things that really went wrong:
  1. the completion event is turn.done, not turn.completed (measured the hard way)
  2. the resume sequence comes from the last event carrying an id, not the last event
  3. an approval request has to stop the read and surface the tool_call_id, never be
     swallowed
"""
from __future__ import annotations

import io
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gate.harness import Event, TurnResult, _consume


def _events(*pairs):
    for seq, typ, data in pairs:
        yield Event(seq=seq, type=typ, data=data)


def test_turn_done_is_the_terminal_event():
    out = _consume(_events(
        (None, "turn.created", {}),
        (1, "model.message", {}),
        (2, "turn.done", {"state": {"output": {"content": "answer"}, "metrics": {"total_tokens": 7}}}),
    ), stop_at_approval=True)
    assert out.output == "answer"
    assert out.metrics == {"total_tokens": 7}


def test_turn_failed_also_terminates():
    out = _consume(_events(
        (1, "model.message", {}),
        (2, "turn.failed", {"state": {}}),
    ), stop_at_approval=True)
    assert out.output is None
    assert len(out.events) == 2


def test_last_seq_skips_events_without_id():
    out = _consume(_events(
        (1, "model.message", {}),
        (2, "model.message.delta", {}),
        (None, "keepalive", {}),
        (3, "turn.done", {"state": {}}),
    ), stop_at_approval=True)
    assert out.last_seq == 3


def test_approval_stops_the_stream_and_keeps_the_call_id():
    out = _consume(_events(
        (1, "model.message", {}),
        (2, "tool.approval_required", {"thread_id": "main", "tool_calls": [{"id": "call_1"}]}),
        (3, "turn.done", {"state": {"output": {"content": "should not be reached"}}}),
    ), stop_at_approval=True)
    assert out.pending_approval is not None
    assert out.pending_approval["tool_calls"][0]["id"] == "call_1"
    assert out.output is None          # stopped at the approval, nothing past it consumed
    assert len(out.events) == 2


def test_approval_can_be_streamed_through_when_resolving():
    out = _consume(_events(
        (1, "tool.approval_required", {"thread_id": "main", "tool_calls": [{"id": "call_1"}]}),
        (2, "turn.done", {"state": {"output": {"content": "done"}}}),
    ), stop_at_approval=False)
    assert out.output == "done"


def test_a_dropped_stream_resumes_from_the_last_sequence(monkeypatch):
    """The connection drops mid-stream: the turn is still running server-side, so resume completes it instead of re-running it."""
    from gate import harness

    calls = []

    def fake_stream(url, body):
        calls.append(url)
        if len(calls) == 1:
            yield Event(1, "turn.created", {"turn_id": "t-1"})
            yield Event(2, "model.message", {})
            raise ConnectionError("connection reset")
        yield Event(3, "tool.response", {})
        yield Event(4, "turn.done", {"state": {"output": {"content": "late answer"},
                                               "metrics": {"total_tokens": 11}}})

    monkeypatch.setattr(harness, "_stream", fake_stream)
    out = harness.run_turn("sess", "hello")

    assert out.output == "late answer"
    assert out.metrics["total_tokens"] == 11
    assert [e.seq for e in out.events] == [1, 2, 3, 4]
    assert "after_sequence_number=2" in calls[1] and "t-1" in calls[1]


def test_reconnect_gives_up_when_the_turn_never_started(monkeypatch):
    """Without even turn.created there is nothing to resume, and retrying only spins."""
    from gate import harness

    calls = []

    def fake_stream(url, body):
        calls.append(url)
        raise ConnectionError("refused")
        yield  # pragma: no cover

    monkeypatch.setattr(harness, "_stream", fake_stream)
    with pytest.raises(harness.HarnessError) as e:
        harness.run_turn("sess", "hello", reconnects=3)

    assert "nothing to resume" in str(e.value)
    assert len(calls) == 1, "no turn_id means nothing to resume, so it must not retry"


def test_reconnect_stops_once_the_turn_is_done(monkeypatch):
    from gate import harness

    calls = []

    def fake_stream(url, body):
        calls.append(url)
        yield Event(1, "turn.created", {"turn_id": "t-1"})
        yield Event(2, "turn.done", {"state": {"output": {"content": "done"}}})

    monkeypatch.setattr(harness, "_stream", fake_stream)
    assert harness.run_turn("sess", "hi").output == "done"
    assert len(calls) == 1


class _FakeResponse:
    """Stand-in for what urlopen returns: iterates byte lines and works as a context manager."""

    def __init__(self, raw: bytes):
        self._lines = raw.splitlines(keepends=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def test_sequence_id_belongs_to_the_event_it_follows(monkeypatch):
    """The harness sends `data:` first and `id:` second, and both belong to the same SSE event.

    Crediting the id to the next event gives every event the previous one's number, leaving the
    resume cursor one short and replaying an event already received.
    """
    from gate import harness

    raw = (b'data: {"type":"turn.created"}\nid: 1\n\n'
           b'data: {"type":"model.message"}\nid: 2\n\n'
           b'data: {"type":"turn.done","state":{}}\nid: 3\n\n')
    monkeypatch.setattr(harness.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResponse(raw))

    events = list(harness._stream("http://x/turns", {"input": []}))
    assert [(e.type, e.seq) for e in events] == [
        ("turn.created", 1), ("model.message", 2), ("turn.done", 3)]


def test_an_http_error_on_the_stream_is_not_mistaken_for_a_dropped_connection(monkeypatch):
    """HTTPError subclasses URLError, so falling into the drop path turns a 400 quietly into "no output"."""
    import urllib.error
    from gate import harness

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                     io.BytesIO(b'{"error":{"message":"bad manifest"}}'))

    monkeypatch.setattr(harness.urllib.request, "urlopen", boom)
    with pytest.raises(harness.HarnessError) as e:
        harness.run_turn("sess", "hello")
    assert "400" in str(e.value) and "bad manifest" in str(e.value)


def test_a_turn_that_never_finished_raises_instead_of_looking_empty(monkeypatch):
    """Exhausting the retries without an ending must not return a result that reads as no output.

    That gets recorded as the subagent failing to answer, writing a connection problem down as
    the model's fault.
    """
    from gate import harness

    def always_drops(url, body):
        yield Event(1, "turn.created", {"turn_id": "t-1"})
        raise ConnectionError("reset")

    monkeypatch.setattr(harness, "_stream", always_drops)
    with pytest.raises(harness.HarnessError) as e:
        harness.run_turn("sess", "hello", reconnects=2)
    assert "t-1" in str(e.value)


def test_a_drop_before_turn_created_recovers_the_turn_id_from_the_session(monkeypatch):
    """The connection drops before turn.created, and the turn is still running on the server.

    Abandoning it burns that call for nothing, and the session can say which turn it was.
    """
    from gate import harness

    calls = []

    def fake_stream(url, body):
        calls.append(url)
        if len(calls) == 1:
            raise ConnectionError("reset before anything arrived")
        yield Event(5, "turn.done", {"state": {"output": {"content": "recovered"}}})

    monkeypatch.setattr(harness, "_stream", fake_stream)
    # Measured: this endpoint lists oldest first. The match is on the message we sent, not on
    # "the last entry", because another turn may be running on the same session.
    def listed(path, body=None, method=None):
        return {"data": [
            {"id": "t-7", "input": [{"type": "user.message", "content": "something else"}]},
            {"id": "t-8", "input": [{"type": "user.message", "content": "hello"}]},
            {"id": "t-9", "input": [{"type": "user.message", "content": "a concurrent turn"}]},
        ]}

    monkeypatch.setattr(harness, "_request", listed)

    out = harness.run_turn("sess", "hello")
    assert out.output == "recovered"
    assert "t-8" in calls[1] and "after_sequence_number=0" in calls[1]


def test_a_dropped_connection_on_a_lookup_is_a_harness_error(monkeypatch):
    """A dropped lookup has to surface as HarnessError, not let URLError reach the caller.

    `_find_turn` and `describe_call` both promise to return None when the value cannot be
    fetched, and both guard that with `except HarnessError`. With no harness running, or on a
    dropped connection, urlopen raises URLError, which skips the guard entirely and takes down
    the approval gate at the point where it looks up a tool name.
    """
    from gate import harness

    def refused(req, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(harness.urllib.request, "urlopen", refused)

    with pytest.raises(harness.HarnessError):
        harness._request("/sessions")

    assert harness._find_turn("sess", "hello") is None
    assert harness.describe_call("sess", "ev-1", "call-1") is None
