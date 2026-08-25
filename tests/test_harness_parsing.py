"""事件流解析的行為測試。

不打真的 harness：SSE 解析與終止條件是純函式邏輯，用假的事件串驗。
這裡咬的是三件真的會出錯的事：
  1. 完成事件叫 turn.done，不是 turn.completed（實測踩過）
  2. 續接用的 sequence 要取最後一個帶 id 的事件，不是最後一個事件
  3. 遇到核准要求時必須停下並交出 tool_call_id，不能吃掉繼續跑
"""
import sys
from pathlib import Path

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
    assert out.output is None          # 停在核准前，沒有把後續吃進來
    assert len(out.events) == 2


def test_approval_can_be_streamed_through_when_resolving():
    out = _consume(_events(
        (1, "tool.approval_required", {"thread_id": "main", "tool_calls": [{"id": "call_1"}]}),
        (2, "turn.done", {"state": {"output": {"content": "done"}}}),
    ), stop_at_approval=False)
    assert out.output == "done"


def test_a_dropped_stream_resumes_from_the_last_sequence(monkeypatch):
    """連線斷在半路：turn 在伺服器那端還在跑，續接要補完而不是重跑整個 turn。"""
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
    """連 turn.created 都沒收到就沒有可續接的對象，重試只會空轉。"""
    from gate import harness

    calls = []

    def fake_stream(url, body):
        calls.append(url)
        raise ConnectionError("refused")
        yield  # pragma: no cover

    monkeypatch.setattr(harness, "_stream", fake_stream)
    out = harness.run_turn("sess", "hello", reconnects=3)

    assert out.output is None
    assert len(calls) == 1


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
    """urlopen 回傳物件的替身：可迭代出位元組行，且是 context manager。"""

    def __init__(self, raw: bytes):
        self._lines = raw.splitlines(keepends=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def test_sequence_id_belongs_to_the_event_it_follows(monkeypatch):
    """harness 送的是 `data:` 在前、`id:` 在後，兩者屬於同一個 SSE 事件。

    把 id 記給下一個事件的話，每個事件都帶前一個的編號，續接游標就會少一格
    並重放一個已經收到的事件。
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
