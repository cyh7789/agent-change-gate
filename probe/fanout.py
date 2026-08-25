import json, pathlib, sys, uuid
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from gate import harness

INSTR = """You triage GitHub issues.

You will receive a numbered list of items. Hand EACH item to its own subagent,
all in parallel. Each subagent's prompt must start with the item's marker line
"ITEM <n>" followed by the item text, and must ask for a one-word answer.
Never answer an item yourself.
When every subagent has replied, output one JSON object mapping "<n>" to the
subagent's one-word answer, nothing else."""

items = [
    "Clicking Save throws TypeError: undefined is not a function.",
    "Please add a dark theme to the settings panel.",
    "The app crashes on startup when the config file is empty.",
    "It would be nice to support exporting to CSV.",
]
prompt = "Classify each item as exactly one of: bug, feature-request\n\n" + "\n".join(
    f"ITEM {i+1}: {t}" for i, t in enumerate(items))

name = f"fanout-{uuid.uuid4().hex[:6]}"
harness.create_agent(name, {
    "model": {"name": "google-gemini/gemini-3-6-flash"},
    "instructions": INSTR,
    "config": {"iteration_limit": 12, "dynamic_sub_agents": {"enabled": True}},
})
sid = harness.create_session(name)
res = harness.run_turn(sid, prompt, stop_at_approval=False)

created = [e for e in res.events if e.type == "thread.created"]
done = [e for e in res.events if e.type == "thread.done"]
print("threads created:", len(created))
for e in created:
    print("  input:", json.dumps(e.data.get("agent_info", {}).get("input", ""))[:90])
print("threads done:", len(done))
for e in done:
    st = e.data.get("state", {})
    out = (st.get("output") or {}).get("content")
    print("  ", st.get("status"), json.dumps(out)[:120])
print("final output:", json.dumps(res.output)[:300])
print("metrics:", res.metrics.get("total_tokens"))
