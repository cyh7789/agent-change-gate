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
