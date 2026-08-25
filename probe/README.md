# Capability probes

Standalone scripts that check what the harness actually does, against a running
TrueForge server on `localhost:8790`. They are how the claims in the README were
established. Run them; don't take the README's word for it.

- `fanout.py`: four items handed to four subagents. Prints each thread's marker
  and output, so two things are visible at once: threads finish in a different
  order than they are created, and every subagent's reply carries the fingerprint
  token the spec under test demands. No fingerprint means the evaluation is not
  running the spec it claims to.
- `codemode.py`: an analyst agent with `config.sandbox.enabled: true` computing
  Wilson intervals. Prints the sandbox id and the tool responses, so you can see
  the code being executed rather than the model narrating a result.
- `instructions_placement.py`: the same spec evaluated twice, once with its rules
  in the agent's `instructions` field and once with them inside the item text,
  paired per scenario. Fan-out can only do the second, because a dynamic subagent
  takes no instructions of its own; this is what says whether that costs anything.
  `PLACEMENT_REPEAT` sets the runs per scenario.

```bash
python3 probe/fanout.py
python3 probe/codemode.py
```

Both create a throwaway agent with a random suffix and need a model provider
configured in the harness.
