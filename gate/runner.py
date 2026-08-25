"""對一份 agent manifest 跑完整組情境，收集結果與 harness 原生 metrics。

情境切成批，每批一個 session、一個 turn，批內由 harness 把每題交給自己的
subagent 去做（`fanout.py`）。批之間序列跑：併發要嘛由 harness 負責，要嘛
不做，不然這支程式又變回「用執行緒去打 HTTP 端點」。

成本與 token 不自己插樁。harness 的 turn.done 事件原生帶 metrics，
連 input 是花在 harness、skills、instructions、tool_definitions 還是 messages 都拆好了。
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Callable

from . import fanout, harness
from .scenarios import Scenario, ScenarioSet


@dataclass
class ScenarioRun:
    scenario_id: str
    output: str | None
    error: str | None = None

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
    batches: list[dict] = field(default_factory=list)

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
        return sum(m.get("total_tokens", 0) for m in self.batches)

    @property
    def output_tokens(self) -> int:
        return sum(m.get("total_output_tokens", 0) for m in self.batches)


def coordinator_manifest(manifest: dict) -> dict:
    """受測 spec 的模型與設定照用，instructions 換成純分派者。

    受測的 instructions 不放在協調者身上：subagent 繼承不到它們，而協調者自己也
    不該按著受測規則作答。那份規則跟著題目走，見 `fanout.batch_prompt`。
    """
    m = copy.deepcopy(manifest)
    m["instructions"] = fanout.DISPATCHER
    cfg = m.setdefault("config", {})
    cfg.setdefault("dynamic_sub_agents", {})["enabled"] = True
    return m


def _batches(scenarios: list[Scenario], repeat: int, size: int):
    jobs = [sc for sc in scenarios for _ in range(max(1, repeat))]
    for i in range(0, len(jobs), size):
        chunk = jobs[i:i + size]
        yield [(n + 1, sc) for n, sc in enumerate(chunk)]


def run_arm(label: str, manifest: dict, scenarios: ScenarioSet,
            batch_size: int = 4, repeat: int = 1,
            on_result: Callable[[ScenarioRun], None] | None = None) -> ArmResult:
    """建一個一次性 agent，對整組情境跑一遍。

    agent 名字帶亂數後綴：同一份 manifest 可能被跑很多次，重名會撞到既有 agent，
    拿到的就不是這次要量的那個設定。
    """
    agent_name = f"{label}-{uuid.uuid4().hex[:8]}"
    instructions = manifest.get("instructions") or ""
    harness.create_agent(agent_name, coordinator_manifest(manifest))
    arm = ArmResult(label=label, agent_name=agent_name)
    ordered = sorted(scenarios.scenarios, key=lambda s: s.id)
    for items in _batches(ordered, repeat, max(1, batch_size)):
        try:
            handled, metrics = fanout.run_batch(agent_name, items, instructions)
        except harness.HarnessError as e:
            handled = [fanout.Handled(sc.id, None, str(e)[:200]) for _, sc in items]
            metrics = {}
        arm.batches.append(metrics)
        for h in handled:
            run = ScenarioRun(h.scenario_id, h.output, h.error)
            arm.runs.append(run)
            if on_result:
                on_result(run)
    arm.runs.sort(key=lambda r: r.scenario_id)
    return arm
