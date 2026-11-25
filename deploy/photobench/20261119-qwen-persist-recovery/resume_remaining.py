"""Resume only the three never-started questions after recovering existing turns."""
from __future__ import annotations

import argparse
import json
import uuid
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
FAILED = '20261119-205809-2026.11.17-atm-sources-addresses-current-resume-4c5f90'
SEED = '20261119-qwen-terminal-turn-recovery-seed-4c5f90'


def api(path, data=None):
    request = urllib.request.Request('http://127.0.0.1:8874/api/v1/' + path,
                                    data=None if data is None else json.dumps(data).encode(),
                                    headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def save(name, value):
    with (HERE / name).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main(wait_id):
    uuid.UUID(wait_id)
    assert (HERE / 'activate-response.json').exists() and (HERE / 'live-write-response.json').exists()
    assert not (HERE / 'resume-intent.json').exists()
    mapping = json.loads((BASE / 'active-runs.json').read_text())
    assert mapping['qwen37']['run_id'] == FAILED
    assert not api('active-work')['work']
    plan = api(f'runs/{SEED}/recovery-plan')
    assert plan['can_resume'] and plan['inherited_qa_count'] == 1010 and plan['remaining_qa_count'] == 3
    assert not plan['retryable_service_failures'] and not plan['product_version_changed']
    assert plan['frozen_scoring_profiles'] == ['atm_qs_v1'] and not plan['judge_use_gt_images']
    source = api(f'runs/{SEED}?include_history=false')
    assert source['scope_id'] == mapping['qwen37']['scope_id']
    assert source['runtime_snapshot']['endpoint_model'] == 'qwen3.7-plus'
    settings = source['runtime_snapshot']['product_settings']
    assert [settings[k] for k in ('reasoning_max_model_calls', 'reasoning_max_tool_calls', 'reasoning_max_search_calls', 'reasoning_max_other_tool_calls')] == [100, 600, 100, 500]
    budgets = source['runtime_snapshot']['execution_budgets']
    assert budgets['qa_concurrency'] == 4 and budgets['turn_timeout'] == 3660
    payload = {'repair_missing_judges': True}
    save('resume-intent.json', {'wait_id': wait_id, 'failed_run': FAILED, 'source_run': SEED,
                               'source': source, 'recovery_plan': plan, 'payload': payload,
                               'note': 'Three existing terminal turns were restored by GET only; only remaining three QA may start Agent.'})
    result = api(f'runs/{SEED}/resume', payload)
    save('resume-response.json', result)
    assert result['resumed_from'] == SEED and result['resumed_items'] == 1010
    assert result['scope_id'] == source['scope_id'] and not result['retried_qa_ids']
    assert json.loads((BASE / 'active-runs.json').read_text()) == mapping
    mapping['qwen37'] = result
    temporary = HERE / 'active-runs.next.json'
    temporary.write_text(json.dumps(mapping, indent=2))
    temporary.replace(BASE / 'active-runs.json')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--wait-id', required=True)
    main(parser.parse_args().wait_id)
