"""核准在介面上的行為：評測執行緒要真的停下來等人按。

按鈕沒接上或事件沒清乾淨的話，畫面看起來還是「等核准」，實際上工具已經放行了。
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gate.state import GateState


def test_ask_blocks_until_someone_decides():
    st = GateState()
    answers = []
    t = threading.Thread(target=lambda: answers.append(st.ask("create_branch")))
    t.start()
    t.join(0.2)
    assert t.is_alive(), "評測執行緒必須停在核准閘前面"
    assert st.snapshot()["pending"] == "create_branch"

    st.decide(True)
    t.join(2)
    assert answers == [True]
    assert st.snapshot()["pending"] is None


def test_a_second_call_waits_again_after_the_first_was_allowed():
    """三個工具呼叫要停三次。事件沒清的話，第二次會直接沿用第一次的答案。"""
    st = GateState()
    answers = []

    def two_calls():
        answers.append(st.ask("create_branch"))
        answers.append(st.ask("create_pull_request"))

    t = threading.Thread(target=two_calls)
    t.start()
    t.join(0.2)
    st.decide(True)
    t.join(0.2)
    assert answers == [True], "第二次呼叫必須重新等人回答"
    assert st.snapshot()["pending"] == "create_pull_request"

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
