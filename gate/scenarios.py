"""The frozen scenario set: loading it, and proving it is intact.

The scenario set is this gate's measuring stick, and a measuring stick that has
been edited proves nothing. Every load recomputes the hash and compares it to the
locked value; a mismatch aborts rather than warns. The source and the hash go into
the report so a reader can fetch the same set and re-run it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


class ScenarioSetTampered(RuntimeError):
    """The scenario set's contents do not match its locked hash."""


@dataclass(frozen=True)
class Scenario:
    id: str
    prompt: str
    expect: dict          # the pass condition; the checks module interprets it


@dataclass(frozen=True)
class ScenarioSet:
    source: str           # where it came from (URL or provenance note), enough to obtain the same set
    revision: str         # the source's version (commit hash, release tag)
    digest: str           # content hash, recomputed and compared on load
    scenarios: tuple[Scenario, ...]

    def __len__(self) -> int:
        return len(self.scenarios)


def compute_digest(scenarios: list[dict]) -> str:
    """Hash the scenario contents. Keys are sorted before serialising so formatting alone never looks like a change."""
    canonical = json.dumps(scenarios, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def load(path: str | Path) -> ScenarioSet:
    raw = json.loads(Path(path).read_text())
    items = raw["scenarios"]
    actual = compute_digest(items)
    locked = raw["digest"]
    if actual != locked:
        raise ScenarioSetTampered(
            f"scenario set digest mismatch: locked {locked[:12]}…, actual {actual[:12]}… "
            f"({path}). The measuring stick changed; refusing to run."
        )
    return ScenarioSet(
        source=raw["source"],
        revision=raw["revision"],
        digest=actual,
        scenarios=tuple(Scenario(id=s["id"], prompt=s["prompt"], expect=s.get("expect", {})) for s in items),
    )


def freeze(source: str, revision: str, scenarios: list[dict], out: str | Path) -> str:
    """Freeze a set of scenarios into a file and return its digest."""
    digest = compute_digest(scenarios)
    Path(out).write_text(json.dumps(
        {"source": source, "revision": revision, "digest": digest, "scenarios": scenarios},
        indent=1, ensure_ascii=False) + "\n")
    return digest
