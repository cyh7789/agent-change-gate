"""事件流解析的行為測試。

不打真的 harness：SSE 解析與終止條件是純函式邏輯，用假的事件串驗。
這裡咬的是三件真的會出錯的事：
  1. 完成事件叫 turn.done，不是 turn.completed（實測踩過）
  2. 續接用的 sequence 要取最後一個帶 id 的事件，不是最後一個事件
  3. 遇到核准要求時必須停下並交出 tool_call_id，不能吃掉繼續跑
"""
from __future__ import annotations

import io
import sys
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
    with pytest.raises(harness.HarnessError) as e:
        harness.run_turn("sess", "hello", reconnects=3)

    assert "nothing to resume" in str(e.value)
    assert len(calls) == 1, "沒有 turn_id 就無從續接，不該重試"


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


def test_an_http_error_on_the_stream_is_not_mistaken_for_a_dropped_connection(monkeypatch):
    """HTTPError 是 URLError 的子類，落進斷線那條路的話，一個 400 會安靜地變成「沒有輸出」。"""
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
    """重試用完還沒收到結局，就不能回一個「沒有輸出」的結果。

    那會被記成「subagent 沒回答」，把一次連線問題寫成模型的錯。
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
    """連線斷在 turn.created 之前，turn 仍在伺服器上跑。

    把 turn 丟掉等於白燒那次呼叫，而 session 這邊查得到它是哪一個。
    """
    from gate import harness

    calls = []

    def fake_stream(url, body):
        calls.append(url)
        if len(calls) == 1:
            raise ConnectionError("reset before anything arrived")
        yield Event(5, "turn.done", {"state": {"output": {"content": "recovered"}}})

    monkeypatch.setattr(harness, "_stream", fake_stream)
    # 實測過：這個端點最舊的排在前面。認的是自己送出去的那段訊息，不是「最後一筆」,
    # 因為同一個 session 上可能有別的 turn 在跑。
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
