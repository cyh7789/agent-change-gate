"""凍結情境集：載入、驗證完整性。

情境集是這個閘門的量尺，量尺自己被改過就什麼都不能證明。所以每次載入都
重算 hash 並比對鎖定值；不符就中止，不是警告。來源與 hash 一起寫進報告，
讓看報告的人可以自己抓同一份重跑。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


class ScenarioSetTampered(RuntimeError):
    """情境集內容與鎖定的 hash 不符。"""


@dataclass(frozen=True)
class Scenario:
    id: str
    prompt: str
    expect: dict          # 判定條件，交給 checks 模組解讀


@dataclass(frozen=True)
class ScenarioSet:
    source: str           # 來源（URL 或出處說明），要能讓別人取得同一份
    revision: str         # 來源的版本（commit hash、release tag 等）
    digest: str           # 內容 hash，載入時重算比對
    scenarios: tuple[Scenario, ...]

    def __len__(self) -> int:
        return len(self.scenarios)


def compute_digest(scenarios: list[dict]) -> str:
    """對情境內容算 hash。鍵排序後序列化，避免格式差異造成假變動。"""
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
    """把一組情境凍結成檔案，回傳 digest。"""
    digest = compute_digest(scenarios)
    Path(out).write_text(json.dumps(
        {"source": source, "revision": revision, "digest": digest, "scenarios": scenarios},
        indent=1, ensure_ascii=False) + "\n")
    return digest
