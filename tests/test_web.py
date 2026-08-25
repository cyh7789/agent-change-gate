"""console 的兩件事：核准端點不是誰都能按，agent 寫的字不會變成這一頁的程式碼。

綁在 127.0.0.1 只擋別台機器。這台機器上任何程式都能 POST /decide，而那個端點
放行的是不可逆的動作，所以要帶只有拿得到頁面的人才有的 token。
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from gate import web
from gate.state import GateState

TOKEN = "test-token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def console():
    state = GateState()
    port = _free_port()
    threading.Thread(target=web.serve, args=(state, port, TOKEN), daemon=True).start()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            break
        except OSError:
            time.sleep(0.02)
    return state, port


def _decide(port: int, token: str | None) -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/decide", data=b'{"allow":true}',
                                 method="POST",
                                 headers={"X-Gate-Token": token} if token else {})
    try:
        urllib.request.urlopen(req, timeout=2)
        return 200
    except urllib.error.HTTPError as e:
        return e.code


def test_approving_without_the_token_is_refused(console):
    state, port = console
    answered = threading.Event()
    threading.Thread(target=lambda: (state.ask("create_branch"), answered.set()),
                     daemon=True).start()
    time.sleep(0.2)

    assert _decide(port, None) == 403
    assert _decide(port, "wrong-token") == 403
    assert state.snapshot()["pending"] == "create_branch", "被拒絕的請求不能放行工具"
    assert not answered.is_set()

    assert _decide(port, TOKEN) == 200
    answered.wait(2)
    assert state.snapshot()["phase"] == "landing"


def test_the_page_carries_the_token_and_no_placeholder(console):
    _, port = console
    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read().decode()
    assert TOKEN in page
    assert "__TOKEN__" not in page


# 跳脫本身沒有測試：它發生在瀏覽器裡，這套測試跑不到 DOM。頁面把 agent 寫的每個值
# 都經過 esc() 之後才插進 innerHTML，人工驗證過；要自動化就得拉一個 headless 瀏覽器
# 進來，那個代價換到的保護不成比例。
