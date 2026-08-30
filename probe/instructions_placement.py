"""Whether the rules in the agent's instructions field and the rules inside the item text measure the same thing.

The harness's dynamic subagents carry no custom instructions (AgentInfo is only
name/input/model), so a fan-out evaluation has to put the rules in the item. In production
they are system instructions. If the two produce different pass rates, the fan-out is trading
validity for a checkbox.

Same spec, same scenarios, N runs each, compared pair by pair.
"""
import json
import os
import pathlib
import sys
import uuid
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from gate import checks, fanout, harness, runner
from gate.scenarios import load

REPEAT = int(os.environ.get("PLACEMENT_REPEAT", "2"))
SPEC = json.loads((pathlib.Path(__file__).resolve().parents[1] / "agents/issue-triage.json").read_text())
SCENARIOS = load(str(pathlib.Path(__file__).resolve().parents[1] / "scenarios/issue-triage.json"))
INSTR = SPEC["instructions"]


def as_system() -> dict[str, list[bool]]:
    """One session per scenario, with the rules in the agent's instructions field."""
    name = f"placement-sys-{uuid.uuid4().hex[:6]}"
    harness.create_agent(name, SPEC)
    out = defaultdict(list)
    for sc in sorted(SCENARIOS.scenarios, key=lambda s: s.id):
        for _ in range(REPEAT):
            sid = harness.create_session(name)
            res = harness.run_turn(sid, sc.prompt, stop_at_approval=True)
            out[sc.id].append(checks.check(res.output, sc.expect)[0])
    return out


def in_the_prompt() -> dict[str, list[bool]]:
    """Fan out, with the rules travelling inside the item."""
    name = f"placement-prompt-{uuid.uuid4().hex[:6]}"
    harness.create_agent(name, runner.coordinator_manifest(SPEC))
    out = defaultdict(list)
    ordered = sorted(SCENARIOS.scenarios, key=lambda s: s.id)
    jobs = [sc for sc in ordered for _ in range(REPEAT)]
    for i in range(0, len(jobs), 4):
        items = [(n + 1, sc) for n, sc in enumerate(jobs[i:i + 4])]
        handled, _ = fanout.run_batch(name, items, INSTR)
        for h, (_, sc) in zip(handled, items):
            out[sc.id].append(checks.check(h.output, sc.expect)[0])
    return out


a, b = as_system(), in_the_prompt()
ap = sum(sum(v) for v in a.values())
bp = sum(sum(v) for v in b.values())
n = sum(len(v) for v in a.values())
print(f"instructions field : {ap}/{n}")
print(f"inside the prompt  : {bp}/{n}")
disagreeing = sum(1 for sid in a for x, y in zip(a[sid], b[sid]) if x != y)
print(f"paired runs: {n}, disagreeing pairs: {disagreeing}")
print("per-scenario disagreements (id, field, prompt):")
for sid in sorted(a):
    if sum(a[sid]) != sum(b[sid]):
        print(f"  {sid}  {sum(a[sid])}/{len(a[sid])}  vs  {sum(b[sid])}/{len(b[sid])}")
