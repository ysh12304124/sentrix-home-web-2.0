import copy
import importlib.util
from pathlib import Path


def gate():
    path = Path(__file__).resolve().parents[4] / "scripts/check_qa_minimal_answers.py"
    spec = importlib.util.spec_from_file_location("minimal_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture():
    module = gate()
    row = {
        "qa_id": "city",
        "question": "哪个城市？",
        "answer": "石家庄",
        "evaluation": {
            "scorer": "choice_judge",
            "correct_option": "A",
            "options": [{"id": "A", "text": "石家庄"}],
        },
    }
    case = {
        "qa_id": "city",
        "minimal_answer": "石家庄",
        "required_facts": ["城市"],
        "review_reason": "题干只问城市",
        "expected_option": "A",
        "contract_sha256": module.contract_sha(row),
    }
    return module, row, case


def test_reviewed_fixture_passes():
    module, row, case = fixture()
    assert module.check([row], [case])["passed"]


def test_option_extra_detail_invalidates_approval():
    module, row, case = fixture()
    row["evaluation"]["options"][0]["text"] += "裕华西路某广场"
    result = module.check([row], [case])
    assert not result["passed"] and "review again" in result["errors"][0]


def test_wrong_key_and_duplicate_cases_fail():
    module, row, case = fixture()
    wrong = copy.deepcopy(case)
    wrong["expected_option"] = "B"
    assert not module.check([row], [wrong])["passed"]
    assert not module.check([row], [case, case])["passed"]


def test_missing_coverage_is_visible():
    module, row, _ = fixture()
    assert module.check([row], [])["unreviewed_choice_ids"] == ["city"]
