# Capability probes

Standalone scripts that check what the harness actually does, against a running
TrueForge server on `localhost:8790`. They are how the claims in the README were
established — run them, don't take the README's word for it.

- `fanout.py` — four items handed to four subagents. Prints each thread's input
  and output, which is where the ordering mismatch shows up: threads finish in a
  different order than they are created.
- `codemode.py` — an analyst agent with `config.sandbox.enabled: true` computing
  Wilson intervals. Prints the sandbox id and the tool responses, so you can see
  the code being executed rather than the model narrating a result.

```bash
python3 probe/fanout.py
python3 probe/codemode.py
```

Both create a throwaway agent with a random suffix and need a model provider
configured in the harness.
