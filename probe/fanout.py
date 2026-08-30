"""Two things about the fan-out, checked against the real harness:

1. Every item really gets its own subagent and its answer maps back to it. Threads finish in
   a different order than they were created, so the mapping cannot go by position.
2. The rules of the spec under test really reach the subagent. The rules here demand a
   fingerprint token at the end of every reply; a subagent output without it means the
   evaluation was not running that spec.
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
