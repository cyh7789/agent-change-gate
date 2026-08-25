"""扇出的兩件事，對真的 harness 驗：

1. 每個題目確實開了自己的 subagent，答案能對回題目（thread 的完成順序跟建立
   順序不同，所以對映不能靠順序）。
2. 受測 spec 的規則確實到了 subagent 身上。這裡的規則要求每則回覆結尾加上一個
   指紋 token；subagent 的輸出沒有它，就表示評測跑的不是那份 spec。
"""
import json
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from gate import fanout, harness, runner
from gate.scenarios import Scenario

FINGERPRINT = "ZX9QQ"
SPEC = {
    "model": {"name": "google-gemini/gemini-3-6-flash"},
    "instructions": f"You triage GitHub issues. Always end every reply with the exact token {FINGERPRINT}.",
    "config": {"iteration_limit": 8},
}
ITEMS = [
    "Clicking Save throws TypeError: undefined is not a function.",
    "Please add a dark theme to the settings panel.",
    "The app crashes on startup when the config file is empty.",
    "It would be nice to support exporting to CSV.",
]

name = f"fanout-{uuid.uuid4().hex[:6]}"
harness.create_agent(name, runner.coordinator_manifest(SPEC))
items = [(i + 1, Scenario(id=f"s{i + 1}", prompt=f"Classify as bug or feature-request: {t}", expect={}))
         for i, t in enumerate(ITEMS)]

sid = harness.create_session(name)
res = harness.run_turn(sid, fanout.batch_prompt(items, SPEC["instructions"]), stop_at_approval=False)

print("threads created:", sum(1 for e in res.events if e.type == "thread.created"))
for t in res.threads:
    print(f"  {t.thread_id[:12]}  marker={fanout._marker_of(t.input)}  "
          f"{FINGERPRINT if FINGERPRINT in (t.output or '') else 'NO-FINGERPRINT':>15}  "
          f"{json.dumps(t.output)[:60]}")

handled, metrics = fanout.run_batch(name, items, SPEC["instructions"])
print("\nmapped back to scenarios:")
for h in handled:
    print(f"  {h.scenario_id}  {json.dumps(h.output)[:50]}  err={h.error}")
carried = [FINGERPRINT in (h.output or "") for h in handled]
print(f"\nspec reached every subagent: {all(carried)}  ({carried})")
print("tokens:", metrics.get("total_tokens"))
