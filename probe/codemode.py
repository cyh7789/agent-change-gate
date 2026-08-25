import json, pathlib, sys, uuid
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from gate import harness

data = {"baseline": [3,3,3,2,3,0,3,3], "candidate": [3,3,2,3,3,3,0,3], "n": 3}
prompt = (
 "Here is a pass-count matrix from an A/B evaluation. Each number is how many of n "
 "repeats passed for one scenario.\n\n" + json.dumps(data) +
 "\n\nWrite and RUN Python code in your sandbox that computes, for each arm, the total "
 "passes, the pass rate, and the Wilson 95% score interval for that rate. Then report "
 "whether the two intervals overlap. Do not compute anything in your head — run the code "
 "and report what it printed. End with a markdown table of the results.")

name = f"codemode-{uuid.uuid4().hex[:6]}"
harness.create_agent(name, {
    "model": {"name": "google-gemini/gemini-3-6-flash"},
    "instructions": "You are a data analyst. You always compute numbers by writing and running code in your sandbox.",
    "config": {"iteration_limit": 12, "sandbox": {"enabled": True, "file_downloads": True}},
})
sid = harness.create_session(name)
res = harness.run_turn(sid, prompt, stop_at_approval=False)
print("event types:", sorted({e.type for e in res.events}))
for e in res.events:
    if e.type == "sandbox.created":
        print("sandbox_id:", e.data.get("sandbox_id"))
    if e.type == "model.message":
        for tc in (e.data.get("tool_calls") or []):
            print("TOOLCALL:", json.dumps(tc)[:400])
    if e.type == "tool.response":
        print("TOOLRESP:", json.dumps(e.data)[:400])
print("---- output ----")
print(res.output)
