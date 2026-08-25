"""一次 gate 執行的即時狀態，給介面讀。

評測本身不知道有沒有人在看：runner 只呼叫 on_result，這裡把那些回呼收成一份
可以被輪詢的快照。核准是唯一反向的一條路：評測執行緒等在一個 Condition 上，
直到有人按下按鈕。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class GateState:
    phase: str = "starting"
    repo: str = ""      # 介面要說出這個決定會碰到哪個 repo
    branch: str = ""
    spec: str = ""      # 正在比的是哪兩份 spec，評測跑的時候畫面上只剩這個
    candidate: str = ""
    scenarios: int = 0
    repeats: int = 1
    runs_done: int = 0
    runs_total: int = 0
    arm: str = ""
    baseline_tokens: int = 0
    candidate_tokens: int = 0
    rows: list = field(default_factory=list)
    # 評測期間的逐題進度：{scenario_id: {arm: [ok, ...]}}
    live: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    verdict: str = ""
    analysis: str | None = None
    pending: dict | None = None
    approvals: int = 0
    # 已經決定過的寫入，照順序：介面把三次寫入畫成一份逐項清單
    decided: list = field(default_factory=list)
    result: str | None = None
    error: str | None = None

    _cond: threading.Condition = field(default_factory=threading.Condition, repr=False)
    _answer: bool | None = None

    def update(self, **fields) -> None:
        with self._cond:
            for k, v in fields.items():
                setattr(self, k, v)

    def count_run(self, arm: str = "", scenario_id: str = "", ok: bool = False) -> None:
        """記下這一次跑完的是哪一題、過了沒。

        評測要跑十幾分鐘，介面上只有一條進度列的話，那段時間畫面等於空的。
        逐題累積下來才看得出扇出是真的在動：十六題各自獨立長出結果。
        """
        with self._cond:
            self.runs_done += 1
            if scenario_id:
                self.live.setdefault(scenario_id, {}).setdefault(arm, []).append(ok)

    def snapshot(self) -> dict:
        with self._cond:
            return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    # --- 核准：評測執行緒等在這裡，介面按鈕解開它 ---

    def ask(self, call: dict) -> bool:
        """擋住呼叫端，直到有人按下核准或拒絕。

        公布 pending、丟掉上一次的答案、開始等待，全在同一把鎖底下：決定要進來就得
        先拿到這把鎖，所以不會有一個答案卡在「已經送出、但還沒開始等」的縫裡消失。
        """
        with self._cond:
            self._answer = None
            self.pending = call
            self.phase = "awaiting-approval"
            self.approvals += 1
            while self._answer is None:
                self._cond.wait()
            allow = self._answer
            self.decided.append({**call, "allowed": allow})
            self.pending = None
            self.phase = "landing" if allow else "rejected"
            return allow

    def decide(self, allow: bool) -> None:
        with self._cond:
            self._answer = allow
            self._cond.notify_all()
