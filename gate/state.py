"""一次 gate 執行的即時狀態，給介面讀。

評測本身不知道有沒有人在看：runner 只呼叫 on_result，這裡把那些回呼收成一份
可以被輪詢的快照。核准是唯一反向的一條路，用一個 Event 擋住執行緒直到有人按下去。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class GateState:
    phase: str = "starting"
    scenarios: int = 0
    repeats: int = 1
    runs_done: int = 0
    runs_total: int = 0
    arm: str = ""
    baseline_tokens: int = 0
    candidate_tokens: int = 0
    rows: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    verdict: str = ""
    analysis: str | None = None
    pending: str | None = None
    approvals: int = 0
    result: str | None = None
    error: str | None = None

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _decided: threading.Event = field(default_factory=threading.Event, repr=False)
    _allow: bool = False

    def update(self, **fields) -> None:
        with self._lock:
            for k, v in fields.items():
                setattr(self, k, v)

    def count_run(self) -> None:
        with self._lock:
            self.runs_done += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    # --- 核准：評測執行緒等在這裡，介面按鈕解開它 ---

    def ask(self, tool_summary: str) -> bool:
        """擋住呼叫端，直到有人按下核准或拒絕。"""
        with self._lock:
            self.pending = tool_summary
            self.phase = "awaiting-approval"
            self.approvals += 1
        self._decided.clear()
        self._decided.wait()
        with self._lock:
            self.pending = None
            self.phase = "landing" if self._allow else "rejected"
            return self._allow

    def decide(self, allow: bool) -> None:
        with self._lock:
            self._allow = allow
        self._decided.set()
