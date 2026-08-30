"""How approval behaves in the console: the evaluation thread really has to stop and wait.

With the button unwired or the event left uncleared, the screen still says awaiting approval
while the tool has already been released.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gate.state import GateState

CALL = {"tool": "create_branch", "server": "github", "input": {"branch": "change-gate/x"}}
PR_CALL = {"tool": "create_pull_request", "server": "github", "input": {"title": "spec change"}}  # the gate publishes a structured call, not a line of text


def test_ask_blocks_until_someone_decides():
    st = GateState()
    answers = []
    t = threading.Thread(target=lambda: answers.append(st.ask(CALL)))
    t.start()
    t.join(0.2)
    assert t.is_alive(), "the evaluation thread has to stop in front of the gate"
    assert st.snapshot()["pending"] == CALL

    st.decide(True)
    t.join(2)
    assert answers == [True]
    assert st.snapshot()["pending"] is None


def test_a_second_call_waits_again_after_the_first_was_allowed():
    """Three tool calls stop three times. With the event uncleared, the second reuses the first answer."""
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
    assert answers == [True], "the second call has to wait for its own answer"
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
    """The agent has used two different tools for the commit, and both have to count.

    Measured: create_or_update_file once, then push_files on the identical request. Recognising
    only one name shows the reader "not committed yet" while that very step waits on them.
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
    """An unfamiliar tool at the gate must not be matched to a row it does not belong to."""
    st = GateState()
    st.update(pending={"tool": "delete_file", "server": "github", "input": {}})
    assert _states(st) == ["todo", "todo", "todo"]


def test_the_snapshot_carries_the_checklist():
    st = GateState()
    st.update(pending={"tool": "create_branch", "server": "github", "input": {}})
    assert st.snapshot()["writes"][0] == {"label": "create the branch", "state": "open"}
