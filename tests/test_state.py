"""核准在介面上的行為：評測執行緒要真的停下來等人按。

按鈕沒接上或事件沒清乾淨的話，畫面看起來還是「等核准」，實際上工具已經放行了。
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gate.state import GateState

CALL = {"tool": "create_branch", "server": "github", "input": {"branch": "change-gate/x"}}
PR_CALL = {"tool": "create_pull_request", "server": "github", "input": {"title": "spec change"}}  # 核准閘公布的是結構化的呼叫，不是一行字


def test_ask_blocks_until_someone_decides():
    st = GateState()
    answers = []
    t = threading.Thread(target=lambda: answers.append(st.ask(CALL)))
    t.start()
    t.join(0.2)
    assert t.is_alive(), "評測執行緒必須停在核准閘前面"
    assert st.snapshot()["pending"] == CALL

    st.decide(True)
    t.join(2)
    assert answers == [True]
    assert st.snapshot()["pending"] is None


def test_a_second_call_waits_again_after_the_first_was_allowed():
    """三個工具呼叫要停三次。事件沒清的話，第二次會直接沿用第一次的答案。"""
    st = GateState()
    answers = []

    def two_calls():
        answers.append(st.ask(CALL))
        answers.append(st.ask(PR_CALL))

    t = threading.Thread(target=two_calls)
    t.start()
    t.join(0.2)
    st.decide(True)
    t.join(0.2)
    assert answers == [True], "第二次呼叫必須重新等人回答"
    assert st.snapshot()["pending"] == PR_CALL

    st.decide(False)
    t.join(2)
    assert answers == [True, False]
    assert st.snapshot()["phase"] == "rejected"


def test_snapshot_leaves_out_the_locking_machinery():
    st = GateState()
    assert all(not k.startswith("_") for k in st.snapshot())


def test_counting_runs_is_safe_from_several_threads():
    st = GateState()
    threads = [threading.Thread(target=lambda: [st.count_run() for _ in range(200)])
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert st.snapshot()["runs_done"] == 1600


def _states(st):
    return [w["state"] for w in st.writes()]


def test_the_checklist_accepts_either_file_write_tool():
    """提交那一步 agent 用過兩個不同的工具，兩個都要算。

    實測：一次 create_or_update_file，下一次同樣的請求變成 push_files。只認一個
    名字的話，人在畫面上會看到「還沒提交」，但它其實正等著他核准那一步。
    """
    for tool in ("create_or_update_file", "push_files"):
        st = GateState()
        st.update(decided=[{"tool": "create_branch", "allowed": True}],
                  pending={"tool": tool, "server": "github", "input": {}})
        assert _states(st) == ["done", "open", "todo"], tool


def test_a_refused_write_does_not_look_like_one_that_never_came():
    st = GateState()
    st.update(decided=[{"tool": "create_branch", "allowed": False}])
    assert _states(st) == ["refused", "todo", "todo"]


def test_all_three_done_reads_as_done():
    st = GateState()
    st.update(decided=[{"tool": "create_branch", "allowed": True},
                       {"tool": "push_files", "allowed": True},
                       {"tool": "create_pull_request", "allowed": True}])
    assert _states(st) == ["done", "done", "done"]


def test_an_unknown_tool_does_not_tick_anything_off():
    """核准閘上冒出沒見過的工具時，清單不能亂認一條。"""
    st = GateState()
    st.update(pending={"tool": "delete_file", "server": "github", "input": {}})
    assert _states(st) == ["todo", "todo", "todo"]


def test_the_snapshot_carries_the_checklist():
    st = GateState()
    st.update(pending={"tool": "create_branch", "server": "github", "input": {}})
    assert st.snapshot()["writes"][0] == {"label": "create the branch", "state": "open"}
