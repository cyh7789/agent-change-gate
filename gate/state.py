"""Live state of one gate run, for the console to read.

The evaluation does not know whether anyone is watching: the runner only calls on_result,
and this module collects those callbacks into a snapshot that can be polled. Approval is
the one path that runs the other way, with the evaluation thread waiting on a Condition
until somebody presses the button.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class GateState:
    phase: str = "starting"
    repo: str = ""      # the console has to name the repo this decision touches
    branch: str = ""
    spec: str = ""      # which two specs are being compared; during the run this is all the screen has
    candidate: str = ""
    source: str = ""    # provenance and digest of the scenario set, so the console shows which measuring stick this is
    digest: str = ""
    batch_size: int = 0
    scenarios: int = 0
    repeats: int = 1
    runs_done: int = 0
    runs_total: int = 0
    arm: str = ""
    baseline_tokens: int = 0
    candidate_tokens: int = 0
    rows: list = field(default_factory=list)
    # per-scenario progress during the run: {scenario_id: {arm: [ok, ...]}}
    live: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    verdict: str = ""
    analysis: str | None = None
    report_md: str = ""   # the console's Copy report reads this instead of going back to disk
    pending: dict | None = None
    approvals: int = 0
    # writes already decided, in order: the console draws the three as a checklist
    decided: list = field(default_factory=list)
    result: str | None = None
    error: str | None = None

    _cond: threading.Condition = field(default_factory=threading.Condition, repr=False)
    _answer: bool | None = None

    def update(self, **fields) -> None:
        with self._cond:
            for k, v in fields.items():
                setattr(self, k, v)

    def count_run(self, arm: str = "", scenario_id: str = "", ok: bool = False) -> None:
        """Record which scenario just finished and whether it passed.

        The evaluation runs for over ten minutes, and a single progress bar leaves the screen
        empty for all of it. Filling in scenario by scenario is what shows the fan-out actually
        working: sixteen scenarios growing results independently.
        """
        with self._cond:
            self.runs_done += 1
            if scenario_id:
                self.live.setdefault(scenario_id, {}).setdefault(arm, []).append(ok)

    def snapshot(self) -> dict:
        with self._cond:
            snap = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        snap["writes"] = self.writes()
        return snap

    # --- Approval: the evaluation thread waits here, the console button releases it ---

    def ask(self, call: dict) -> bool:
        """Block the caller until somebody approves or rejects.

        Publishing the pending call, clearing the previous answer and starting to wait all
        happen under one lock: a decision has to take that lock to arrive, so no answer can
        land in the gap between "published" and "waiting" and be erased.
        """
        with self._cond:
            self._answer = None
            self.pending = call
            self.phase = "awaiting-approval"
            self.approvals += 1
            while self._answer is None:
                self._cond.wait()
            allow = self._answer
            self.decided.append({**call, "allowed": allow})
            self.pending = None
            self.phase = "landing" if allow else "rejected"
            return allow

    # The three writes a landing goes through. Two tools can perform the commit and the agent
    # has used both (measured: create_or_update_file once, push_files once), so recognising
    # only one name leaves that row showing as never done.
    WRITES = (("create the branch", ("create_branch",)),
              ("commit the new spec", ("create_or_update_file", "push_files")),
              ("open the pull request", ("create_pull_request",)))

    def writes(self) -> list[dict]:
        """Where each of the three writes stands, for the console's checklist.

        States are done / refused / open / todo. A refusal has to stay visible and must not
        look like a step that has not come up yet: the reader needs to tell "I rejected this"
        from "this has not been asked".
        """
        with self._cond:
            decided, pending = list(self.decided), self.pending
        out = []
        for label, tools in self.WRITES:
            answered = next((d for d in decided if d.get("tool") in tools), None)
            if answered:
                state = "done" if answered.get("allowed") else "refused"
            elif pending and pending.get("tool") in tools:
                state = "open"
            else:
                state = "todo"
            out.append({"label": label, "state": state})
        return out

    def decide(self, allow: bool) -> None:
        with self._cond:
            self._answer = allow
            self._cond.notify_all()
