"""核准閘上顯示的東西：從 model.message 還原出工具名稱與參數。

事件外形取自實測（`GET /sessions/{id}/events`）：MCP 的呼叫外面包一層 call_tool，
真正的工具名在 `function.arguments` 的 `tool_name` 裡。
"""
from __future__ import annotations

import pytest

from gate import harness

EVENT = {
    "id": "01m0xedyw3jp21fss4g93zpg3q",
    "type": "model.message",
    "tool_calls": [{
        "id": "call_792298",
        "type": "function",
        "function": {
            "name": "call_tool",
            "arguments": ('{"mcp_server":"github","input":{"owner":"cyh7789",'
                          '"branch":"change-gate/final","repo":"agent-change-gate"},'
                          '"tool_name":"create_branch"}'),
        },
    }],
}


@pytest.fixture
def events(monkeypatch):
    def use(rows):
        monkeypatch.setattr(harness, "_request", lambda *a, **k: {"data": rows})
    return use


def test_names_the_tool_and_its_arguments(events):
    events([{"turn_id": "t1", "event": EVENT}])
    got = harness.describe_call("s1", EVENT["id"], "call_792298")
    assert got.startswith("github.create_branch")
    assert "branch=change-gate/final" in got
    assert "repo=agent-change-gate" in got


def test_events_may_come_unwrapped(events):
    events([EVENT])
    assert harness.describe_call("s1", EVENT["id"], "call_792298").startswith("github.create_branch")


def test_wrong_call_id_on_the_right_event_is_not_a_match(events):
    events([{"event": EVENT}])
    assert harness.describe_call("s1", EVENT["id"], "call_000000") is None


def test_unknown_event_gives_up_instead_of_guessing(events):
    events([{"event": EVENT}])
    assert harness.describe_call("s1", "no-such-event", "call_792298") is None


def test_unparseable_arguments_fall_back_to_the_function_name(events):
    broken = {**EVENT, "tool_calls": [{**EVENT["tool_calls"][0],
                                       "function": {"name": "call_tool", "arguments": "{oops"}}]}
    events([{"event": broken}])
    assert harness.describe_call("s1", EVENT["id"], "call_792298") == "call_tool"


def test_harness_unreachable_is_not_an_error(monkeypatch):
    def boom(*a, **k):
        raise harness.HarnessError("down")
    monkeypatch.setattr(harness, "_request", boom)
    assert harness.describe_call("s1", "e1", "call_1") is None


def test_long_and_non_scalar_arguments_stay_off_the_card(events):
    args = ('{"mcp_server":"github","tool_name":"create_or_update_file",'
            '"input":{"path":"agents/issue-triage.json","content":"' + "x" * 90 + '",'
            '"extra":{"nested":1}}}')
    call = {**EVENT["tool_calls"][0],
            "function": {"name": "call_tool", "arguments": args}}
    events([{"event": {**EVENT, "tool_calls": [call]}}])
    got = harness.describe_call("s1", EVENT["id"], "call_792298")
    assert "path=agents/issue-triage.json" in got
    assert "xxxx" not in got and "nested" not in got
