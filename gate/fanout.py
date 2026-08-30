"""Hand a batch of scenarios to the harness's subagents instead of running threads here.

The difference is not only who schedules the work. A Python thread pool calling in
parallel reduces the harness to an HTTP endpoint; handing the items to subagents puts
the fan-out, the scheduling and the context isolation in the harness.

**Subagents do not inherit the parent agent's instructions.** Measured: with the parent's
instructions demanding a fingerprint token at the end of every reply, only the
coordinator's output carried it and neither subagent did. So the spec under test travels
with each item, written into the item's own text, and the coordinator forwards items
verbatim without carrying any rules of its own, which is also why it cannot contaminate
the thing being measured.

Answers are mapped back by thread_id plus a marker at the top of the prompt. Subagents
finish in a different order than they were created, so matching by position attaches
answers to the wrong scenarios.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import harness
from .scenarios import Scenario

MARKER = "ITEM"

DISPATCHER = """You dispatch work. You do not do it.

You will receive a numbered list of items, each starting with a line "ITEM <n>"
and running until the next "ITEM" line. Hand EVERY item to its own subagent, all
of them in parallel, passing that item's text through verbatim and complete: the
"ITEM <n>" line, and every line under it, unchanged. The text already contains
everything the subagent needs.

Never answer an item yourself, never merge two items, never summarise or reword
an item, and never add instructions of your own. Once every subagent has replied,
output their answers and nothing else."""


@dataclass
class Handled:
    scenario_id: str
    output: str | None
    error: str | None = None


def batch_prompt(items: list[tuple[int, Scenario]], instructions: str) -> str:
    """Every item carries the spec under test, because a subagent inherits none of it."""
    head = instructions.strip()
    return "\n\n".join(f"{MARKER} {n}\n{head}\n\n{sc.prompt}" for n, sc in items)


def _marker_of(text: str) -> int | None:
    """Only the marker that sits alone on its own line counts.

    The rules and the issue text both live inside the item, so a passing mention of
    "ITEM 3" is likely. Without the anchor, that sentence steals the answer for another
    scenario and the report still looks normal.
    """
    m = re.search(rf"^{MARKER}\s+(\d+)\s*$", text or "", re.MULTILINE)
    return int(m.group(1)) if m else None


def run_batch(agent_name: str, items: list[tuple[int, Scenario]],
              instructions: str) -> tuple[list[Handled], dict]:
    """Run one batch, returning per-scenario results and the turn's own metrics.

    The integer in `items` is the position within the batch, used to map the prompt a
    subagent received back to its scenario. The same scenario appearing twice in one
    batch gets two different numbers, so the two answers cannot overwrite each other.
    """
    sid = harness.create_session(agent_name)
    res = harness.run_turn(sid, batch_prompt(items, instructions), stop_at_approval=True)
    if res.pending_approval is not None:
        return ([Handled(sc.id, None, "paused for approval during evaluation")
                 for _, sc in items], res.metrics)

    by_marker: dict[int, str | None] = {}
    for th in res.threads:
        n = _marker_of(th.input)
        if n is not None and n not in by_marker:
            by_marker[n] = th.output if th.status == "done" else None

    out = []
    for n, sc in items:
        if n in by_marker and by_marker[n]:
            out.append(Handled(sc.id, by_marker[n]))
        else:
            out.append(Handled(sc.id, None, "no subagent returned an answer for this item"))
    return out, res.metrics
