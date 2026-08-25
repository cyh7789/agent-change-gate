"""對真的 harness 驗續接：讀幾個事件就把連線砍掉，看 run_turn 有沒有把 turn 補完。

斷線是包住 `_stream` 製造的（真實的網路斷線不好按需重現），但斷點之後的一切
都是真的：turn 在伺服器那端繼續跑，續接走 subscribe 端點，sequence 由 harness 給。
"""
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from gate import harness

CUT_AFTER = 3

original = harness._stream
cut_once = {"done": False}


def flaky_stream(url, body):
    n = 0
    for ev in original(url, body):
        yield ev
        n += 1
        if not cut_once["done"] and n >= CUT_AFTER:
            cut_once["done"] = True
            print(f"  ✂ dropping the connection after {n} events (seq {ev.seq})")
            raise ConnectionError("simulated drop")


harness._stream = flaky_stream

name = f"reconnect-{uuid.uuid4().hex[:6]}"
harness.create_agent(name, {
    "model": {"name": "google-gemini/gemini-3-6-flash"},
    "instructions": "You answer briefly.",
    "config": {"iteration_limit": 6},
})
sid = harness.create_session(name)
res = harness.run_turn(sid, "List the first 8 prime numbers, one per line, nothing else.")

seqs = [e.seq for e in res.events if e.seq is not None]
print("turn_id:", res.turn_id)
print("events:", len(res.events), "seq range:", seqs[0], "→", seqs[-1])
print("duplicate seqs:", len(seqs) - len(set(seqs)))
print("finished:", res.finished)
print("output:", (res.output or "").replace("\n", " ")[:80])
