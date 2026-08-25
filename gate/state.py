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
    source: str = ""    # 情境集的出處與 digest，介面上要看得到這次量的是哪一把尺
    digest: str = ""
    batch_size: int = 0
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
    report_md: str = ""   # 介面的 Copy report 從這裡拿，不用再讀一次磁碟
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
            snap = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        snap["writes"] = self.writes()
        return snap

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

    # 落地要走的三個寫入。提交那一步有兩個工具做得到，agent 兩個都用過
    # （實測：一次 create_or_update_file、一次 push_files），只認一個名字會讓
    # 那一條在介面上永遠顯示成還沒做。
    WRITES = (("create the branch", ("create_branch",)),
              ("commit the new spec", ("create_or_update_file", "push_files")),
              ("open the pull request", ("create_pull_request",)))

    def writes(self) -> list[dict]:
        """三個寫入各自到哪一步了，給介面畫成逐項清單。

        狀態是 done / refused / open / todo：拒絕過的要留著，不能跟還沒到的長一樣，
        看的人得分得出「我按了拒絕」和「還沒輪到」。
        """
        with self._cond:
            decided, pending = list(self.decided), self.pending
        out = []
        for label, tools in self.WRITES:
            answered = next((d for d in decided if d.get("tool") in tools), None)
            if answered:
                state = "done" if answered.get("allowed") else "refused"
            elif pending and pending.get("tool") in tools:
                state = "open"
            else:
                state = "todo"
            out.append({"label": label, "state": state})
        return out

    def decide(self, allow: bool) -> None:
        with self._cond:
            self._answer = allow
            self._cond.notify_all()
