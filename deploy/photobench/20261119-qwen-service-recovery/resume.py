"""Resume seven verified Qwen transport failures once, retaining the frozen protocol."""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.request
import uuid
from pathlib import Path

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
HERE = Path(__file__).resolve().parent
SOURCE = '20261119-151029-2026.11.17-atm-sources-addresses-current-resume-f41436'
SCOPE = 'PhotoBench-20261118-043128-2026.11.17-atm-sources-addresses-current'
RETRY = [
    'atm-1e644e18-6645-43fa-bb0c-156d88692427',
    'atm-b767821e-3d20-4880-8de0-81c6bedf1577',
    'atm-3aff6eaa-f88c-4301-bca8-cd9643331cfb',
    'atm-13ec73ea-7213-4877-ab16-17211ac39982',
    'atm-2a3fce70-b412-4ae1-a193-8b87873d4ebf',
    'atm-171315b3-d61b-4ad7-9d0d-ecaf0a55262d',
    'atm-ed583465-4d70-488a-aad1-795b1780df41',
]
BOUNDS = {
    'reasoning_max_model_calls': 100,
    'reasoning_max_tool_calls': 600,
    'reasoning_max_search_calls': 100,
    'reasoning_max_other_tool_calls': 500,
}


def api(path, payload=None, port=8874):
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/v1/{path}',
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def save(name, value):
    with (HERE / name).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def resume(wait_id):
    uuid.UUID(wait_id)
    assert not (HERE / 'resume-intent.json').exists(), 'single-use intent already exists'
    mapping = json.loads((BASE / 'active-runs.json').read_text())
    assert mapping['qwen37']['run_id'] == SOURCE
    source = api(f'runs/{SOURCE}?include_history=false')
    assert source['status'] == 'completed_with_errors' and source['item_count'] == 1013
    assert source['scope_id'] == SCOPE and source['storage_format'] == 3
    assert source['reasoning'] is True and source['graph_enabled'] is True
    runtime = source['runtime_snapshot']
    assert runtime['endpoint_model'] == 'qwen3.7-plus'
    assert runtime['model_base_url'] == 'http://127.0.0.1:18108/v1'
    assert runtime['sentrix_url'] == 'http://127.0.0.1:8294'
    assert all(runtime['product_settings'][k] == v for k, v in BOUNDS.items())
    budgets = runtime['execution_budgets']
    assert budgets['qa_concurrency'] == 4 and budgets['turn_timeout'] == 3660
    assert not api('active-work')['work']
    database = Path('/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db')
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as db:
        assert not db.execute(
            "SELECT id FROM agent_turns WHERE scope_id=? AND status IN ('pending','running')",
            (SCOPE,),
        ).fetchall()
    progress = api(f'imports/progress?scope_id={SCOPE}', port=8294)
    assert not progress['library']['processing'] and progress['postprocess']['state'] == 'idle'
    with urllib.request.urlopen('http://127.0.0.1:18108/v1/models', timeout=15) as response:
        assert json.load(response)['data'][0]['id'] == 'qwen3.7-plus'
    plan = api(f'runs/{SOURCE}/recovery-plan')
    assert plan['can_resume'] and plan['inherited_qa_count'] == 1013
    assert plan['remaining_qa_count'] == 0 and plan['scope_id'] == SCOPE
    assert {r['qa_id'] for r in plan['retryable_service_failures']} == set(RETRY)
    assert plan['frozen_scoring_profiles'] == ['atm_qs_v1']
    assert not plan['product_version_changed'] and not plan['judge_use_gt_images']
    payload = {
        'retry_qa_ids': RETRY,
        'retry_reason': 'Recover verified transport timeouts/HTTP502 without delivered answers; retain source failure history.',
        'repair_missing_judges': True,
    }
    save('resume-intent.json', {
        'wait_id': wait_id, 'source': source, 'recovery_plan': plan, 'payload': payload,
        'missing_score_qa_ids': [
            'atm-71582c85-dbc5-4460-b549-66d323ec362a',
            'atm-bfe15c10-8ee3-41ed-87a8-c134dbd196ec',
        ],
        'note': 'The two existing unscored answers require separate profile scoring after resume.',
    })
    # Submit once. An uncertain response must be reconciled from the saved intent.
    result = api(f'runs/{SOURCE}/resume', payload)
    save('resume-response.json', result)
    assert result['resumed_from'] == SOURCE and result['scope_id'] == SCOPE
    assert result['resumed_items'] == 1006 and set(result['retried_qa_ids']) == set(RETRY)
    assert result['trace_quarantined_items'] == 0
    current = json.loads((BASE / 'active-runs.json').read_text())
    assert current == mapping, 'active mapping changed during submission'
    current['qwen37'] = result
    temporary = HERE / 'active-runs.next.json'
    temporary.write_text(json.dumps(current, indent=2))
    temporary.replace(BASE / 'active-runs.json')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--wait-id', required=True)
    resume(parser.parse_args().wait_id)
