"""Two things about the console: not everyone can press approve, and text written by an agent never becomes code on this page.

Binding to 127.0.0.1 only keeps other machines out. Anything on this machine can POST /decide,
and that endpoint releases irreversible actions, so it takes a token only whoever can load the
page has.
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

CALL = {"tool": "create_branch", "server": "github", "input": {"branch": "change-gate/x"}}  # the gate publishes a structured call, not a line of text

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
    threading.Thread(target=lambda: (state.ask(CALL), answered.set()),
                     daemon=True).start()
    time.sleep(0.2)

    assert _decide(port, None) == 403
    assert _decide(port, "wrong-token") == 403
    assert state.snapshot()["pending"] == CALL, "a rejected request must not release the tool"
    assert not answered.is_set()

    assert _decide(port, TOKEN) == 200
    answered.wait(2)
    assert state.snapshot()["phase"] == "landing"


def test_the_page_carries_the_token_and_no_placeholder(console):
    _, port = console
    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read().decode()
    assert TOKEN in page
    assert "__TOKEN__" not in page


# The escaping itself has no test: it happens in the browser and this suite never reaches the
# DOM. Every agent-written value goes through esc() before touching innerHTML, verified by
# hand. Automating it means pulling in a headless browser, which buys less protection than it
# costs.
