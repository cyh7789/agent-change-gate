"""把一批情境交給 harness 的 subagent 去跑，而不是自己開執行緒。

差別不只是誰在排程。用 Python 的執行緒池平行呼叫，harness 只是被當成 HTTP 端點；
交給 subagent，扇出、排程、上下文隔離都是 harness 在做。

**subagent 不繼承父 agent 的 instructions**（實測：父 agent 的 instructions 要求每則
回覆結尾加上一個指紋 token，只有協調者的輸出帶著它，兩個 subagent 都沒有）。所以
受測 spec 的 instructions 必須跟著題目一起走，寫在每個 item 的文字裡；協調者只負責
把 item 原文轉交，自己不帶受測規則，也就污染不了受測條件。

對映走 thread_id 與 prompt 開頭的 marker：subagent 的完成順序跟建立順序不同，
照順序配會把答案掛到錯的情境上。
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
    """每個 item 自帶受測 spec 的 instructions，因為 subagent 繼承不到它們。"""
    head = instructions.strip()
    return "\n\n".join(f"{MARKER} {n}\n{head}\n\n{sc.prompt}" for n, sc in items)


def _marker_of(text: str) -> int | None:
    m = re.search(rf"{MARKER}\s+(\d+)", text or "")
    return int(m.group(1)) if m else None


def run_batch(agent_name: str, items: list[tuple[int, Scenario]],
              instructions: str) -> tuple[list[Handled], dict]:
    """跑一批，回傳每題結果與這個 turn 的原生 metrics。

    items 的整數是批內編號，用來把 subagent 收到的 prompt 對回情境；同一個情境
    在同一批出現兩次時編號不同，兩次的答案才不會互相蓋掉。
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
