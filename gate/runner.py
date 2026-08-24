"""對一份 agent manifest 跑完整組情境，收集結果與 harness 原生 metrics。

每個情境開自己的 session：情境之間不共享上下文，一個情境的對話不會污染下一個。
情境彼此獨立且唯讀，所以平行跑；併發數留給呼叫端決定，預設保守。

成本與 token 不自己插樁 —— harness 的 turn.done 事件原生帶 metrics，
連 input 是花在 harness、skills、instructions、tool_definitions 還是 messages 都拆好了。
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from . import harness
from .scenarios import Scenario, ScenarioSet


@dataclass
class ScenarioRun:
    scenario_id: str
    output: str | None
    metrics: dict = field(default_factory=dict)
    error: str | None = None
    session_id: str | None = None
    turn_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.output)


@dataclass
class ArmResult:
    """一組（基準或候選）跑完整組情境的結果。

    同一個情境可能被跑很多次：模型輸出不是確定性的，實測同一份 spec 連跑三次，
    16 題裡有 1 題給出 pass/fail/fail。只跑一次的話，那一題會被當成變更造成的
    退步報上去，而它跟變更無關。
    """
    label: str
    agent_name: str
    runs: list[ScenarioRun] = field(default_factory=list)

    def for_scenario(self, scenario_id: str) -> list[ScenarioRun]:
        return [r for r in self.runs if r.scenario_id == scenario_id]

    @property
    def repeats(self) -> int:
        if not self.runs:
            return 0
        first = self.runs[0].scenario_id
        return sum(1 for r in self.runs if r.scenario_id == first)

    @property
    def completed(self) -> int:
        return sum(1 for r in self.runs if r.ok)

    @property
    def total_tokens(self) -> int:
        return sum(r.metrics.get("total_tokens", 0) for r in self.runs)

    @property
    def output_tokens(self) -> int:
        return sum(r.metrics.get("total_output_tokens", 0) for r in self.runs)


def _run_one(agent_name: str, sc: Scenario) -> ScenarioRun:
    try:
        sid = harness.create_session(agent_name)
        res = harness.run_turn(sid, sc.prompt, stop_at_approval=True)
        turn_id = next((e.data.get("turn_id") for e in res.events if e.type == "turn.created"), None)
        if res.pending_approval is not None:
            return ScenarioRun(sc.id, None, {}, "paused for approval during evaluation", sid, turn_id)
        return ScenarioRun(sc.id, res.output, res.metrics, None, sid, turn_id)
    except harness.HarnessError as e:
        return ScenarioRun(sc.id, None, {}, str(e)[:200])


def run_arm(label: str, manifest: dict, scenarios: ScenarioSet,
            concurrency: int = 4, repeat: int = 1,
            on_result: Callable[[ScenarioRun], None] | None = None) -> ArmResult:
    """建一個一次性 agent，對整組情境跑一遍。

    agent 名字帶亂數後綴：同一份 manifest 可能被跑很多次，重名會撞到既有 agent，
    拿到的就不是這次要量的那個設定。
    """
    agent_name = f"{label}-{uuid.uuid4().hex[:8]}"
    harness.create_agent(agent_name, manifest)
    arm = ArmResult(label=label, agent_name=agent_name)
    jobs = [sc for sc in scenarios.scenarios for _ in range(max(1, repeat))]
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for run in pool.map(lambda sc: _run_one(agent_name, sc), jobs):
            arm.runs.append(run)
            if on_result:
                on_result(run)
    arm.runs.sort(key=lambda r: r.scenario_id)
    return arm
