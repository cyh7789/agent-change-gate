"""Run one agent manifest against the whole scenario set, collecting results and the harness's own metrics.

Scenarios are cut into batches, one session and one turn each, and within a batch the harness
hands every scenario to its own subagent (`fanout.py`). Batches run one after another:
concurrency is either the harness's job or nobody's, otherwise this file goes back to being
threads hitting an HTTP endpoint.

Cost and tokens are not instrumented here. The harness's turn.done event carries metrics
already, down to whether input was spent on harness, skills, instructions, tool_definitions
or messages.
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
    """One arm's results (baseline or candidate) over the whole scenario set.

    A scenario can be run many times, because model output is not deterministic: measured on
    three consecutive runs of the same spec, 1 of the 16 scenarios answered pass/fail/fail.
    With a single run, that scenario gets reported as a regression caused by the change, when
    it has nothing to do with the change.
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
    """Keep the spec under test's model and config, replace its instructions with a pure dispatcher.

    The instructions under test do not sit on the coordinator: subagents inherit none of them,
    and the coordinator should not be answering by the rules being measured either. Those rules
    travel with the item; see `fanout.batch_prompt`.
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
    """Create a throwaway agent and take it once through the whole scenario set.

    The random suffix on the agent name matters: the same manifest may be run many times, and a
    repeated name collides with an existing agent, so what gets measured is not the config
    intended for this run.
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
