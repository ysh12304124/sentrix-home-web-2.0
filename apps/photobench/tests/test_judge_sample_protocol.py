"""Sample parsing and production input construction without HTTP dispatch."""

import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).parent / "fixtures" / "judge-regression"
SAMPLE = runpy.run_path(str(ROOT / "run_samples.py"))


def response(value):
    return {"choices": [{"message": {"content": json.dumps(value)}}]}


@pytest.mark.parametrize("value", [True, "true", False, "false"])
def test_official_boolean_strings_match_application_parser(value):
    score, _ = SAMPLE["parse_score"](
        {"profile_id": "atm_qs_v1", "qtype": "open_end"}, response({"accuracy": value})
    )
    assert score == int(str(value).lower() == "true")


def test_missing_or_invalid_score_remains_unscored():
    with pytest.raises(ValueError):
        SAMPLE["parse_score"]({"profile_id": "atm_qs_v1", "qtype": "number"}, response({"score": True}))
    with pytest.raises(ValueError):
        SAMPLE["parse_score"]({"profile_id": "photobench_open_v1"}, response({"score": None}))


def test_open_sample_uses_real_task_v4_payload_and_keeps_official_scope():
    fixtures = json.loads((ROOT / "cases-v1.json").read_text())
    prompts = json.loads((ROOT / "prompts-v1.json").read_text())
    connection = {"url": "https://ark.cn-beijing.volces.com/api/plan/v3", "model": "fixed"}
    case = next(c for c in fixtures["cases"] if c["id"] == "open012-partial")
    payload = SAMPLE["build_request"](case, "current", prompts, connection)
    data = json.loads(payload["messages"][1]["content"])
    assert payload["max_tokens"] == 1024
    assert data["delivered_media_aliases"] == data["tool_media_aliases"] == []
    assert "工具编号核对" in payload["messages"][0]["content"]
    numeric = next(c for c in fixtures["cases"] if c["id"] == "date-zh")
    with pytest.raises(ValueError, match="code"):
        SAMPLE["build_request"](numeric, "official_open", prompts, connection)
