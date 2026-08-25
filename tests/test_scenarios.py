"""情境集完整性的行為測試：量尺被動過就必須拒跑。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gate.scenarios import ScenarioSetTampered, compute_digest, freeze, load

ITEMS = [
    {"id": "s1", "prompt": "one", "expect": {"contains": "a"}},
    {"id": "s2", "prompt": "two", "expect": {"contains": "b"}},
]


def test_freeze_then_load_roundtrips(tmp_path):
    f = tmp_path / "set.json"
    digest = freeze("https://example.org/set", "abc123", ITEMS, f)
    s = load(f)
    assert len(s) == 2
    assert s.digest == digest
    assert s.source == "https://example.org/set"
    assert s.scenarios[0].id == "s1"


def test_editing_a_prompt_is_refused(tmp_path):
    f = tmp_path / "set.json"
    freeze("src", "rev", ITEMS, f)
    raw = json.loads(f.read_text())
    raw["scenarios"][0]["prompt"] = "tampered"     # 改內容但不改 digest
    f.write_text(json.dumps(raw))
    with pytest.raises(ScenarioSetTampered):
        load(f)


def test_adding_a_scenario_is_refused(tmp_path):
    f = tmp_path / "set.json"
    freeze("src", "rev", ITEMS, f)
    raw = json.loads(f.read_text())
    raw["scenarios"].append({"id": "s3", "prompt": "three", "expect": {}})
    f.write_text(json.dumps(raw))
    with pytest.raises(ScenarioSetTampered):
        load(f)


def test_key_order_does_not_change_the_digest():
    a = [{"id": "s1", "prompt": "one", "expect": {}}]
    b = [{"prompt": "one", "expect": {}, "id": "s1"}]
    assert compute_digest(a) == compute_digest(b)
