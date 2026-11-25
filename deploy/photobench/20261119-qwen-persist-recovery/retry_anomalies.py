"""One authorized 17-QA attempt, retaining the original run and all its scores.

plan is read-only except its audit file. seed and resume are separate single-use
operations, permitted only after the six-answer Judge activity has stopped.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import resource
import sqlite3
import urllib.request
import uuid

from photobench.domain.evaluation.profiles import summarize_batches, valid_result
from photobench.domain.runs.execution import agent_completion_error
from photobench.infrastructure.results.split_store import SplitResultWriter, SplitRunReader

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
ROOT = BASE / 'cloud-results'
HERE = Path(__file__).resolve().parent
SOURCE = '20261119-213836-2026.11.17-atm-sources-addresses-current-resume-0e3686'
SEED = '20261119-qwen-authorized-anomaly17-attempt-seed-0e3686'
BATCH = 'score-dd0413c8aa869b1c84626de7'
EXPECTED = {'step_budget_exhausted': 11, 'planner_blocked': 2, 'guard_blocked': 2,
            'timeout': 1, 'model_error': 1}


def api(path, data=None, port=8874):
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/v1/' + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def save(name, value):
    with (HERE / name).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def manifest_sha(run):
    return hashlib.sha256((ROOT / run / 'split-commit.json').read_bytes()).hexdigest()


def source():
    assert json.loads((BASE / 'active-runs.json').read_text())['qwen37']['run_id'] == SOURCE
    reader = SplitRunReader(ROOT, ROOT / SOURCE)
    state = reader.state()
    assert state['status'] == 'completed_with_errors' and len(state['items']) == state['qa_count'] == 1013
    assert state['reasoning'] and state['graph_enabled']
    runtime = state['runtime_snapshot']
    assert runtime['endpoint_model'] == 'qwen3.7-plus'
    settings = runtime['product_settings']
    assert [settings[k] for k in ('reasoning_max_model_calls', 'reasoning_max_tool_calls',
        'reasoning_max_search_calls', 'reasoning_max_other_tool_calls')] == [100, 600, 100, 500]
    assert runtime['execution_budgets']['qa_concurrency'] == 4
    assert runtime['execution_budgets']['turn_timeout'] == 3660
    return reader, state


def idle(state):
    assert not api('active-work')['work'], 'evaluator still has active work'
    progress = api('imports/progress?scope_id=' + state['scope_id'], port=8294)
    assert progress['postprocess']['state'] == 'idle' and not progress['library']['processing']
    database = '/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db'
    with sqlite3.connect('file:' + database + '?mode=ro', uri=True) as db:
        assert not db.execute("SELECT id FROM agent_turns WHERE scope_id=? AND status IN ('pending','running')",
                              (state['scope_id'],)).fetchall(), 'product turns still active'


def plan():
    reader, state = source()
    selected = [item for item in state['items'] if agent_completion_error(item)]
    assert len(selected) == 17
    assert Counter(item['termination_reason'] for item in selected) == EXPECTED
    errors = (state['phases']['qa_eval'].get('error_details') or [])
    assert {e['sample_id'] for e in errors if e['error_type'] == 'agent_error'} == {i['qa_id'] for i in selected}
    order = {r['qa_id']: n for n, r in enumerate(state['qa_snapshot']['rows'])}
    save('anomaly17-plan.json', {'source_run_id': SOURCE, 'seed_run_id': SEED,
        'source_revision_at_plan': reader.revision, 'scope_id': state['scope_id'],
        'authorization': 'User explicitly authorized this batch of 17 Agent anomalies to run as new attempts on latest code; preserve original run, failures and valid scores.',
        'counts': EXPECTED, 'qa_ids': [i['qa_id'] for i in selected],
        'original_attempts': [{'qa_id': i['qa_id'], 'original_index': order[i['qa_id']],
            'termination_reason': i['termination_reason'], 'execution_status': i['execution_status'],
            'item_sha256': reader.reader.fingerprint(i),
            'answer_sha256': hashlib.sha256((i.get('answer') or '').encode()).hexdigest()}
            for i in selected], 'source_scores_remain_in_original_run': True})
    print(json.dumps({'targets': len(selected), 'counts': EXPECTED}))


def seed():
    assert not (HERE / 'anomaly17-seed-intent.json').exists()
    assert not (ROOT / SEED).exists()
    plan = json.loads((HERE / 'anomaly17-plan.json').read_text())
    reader, state = source()
    idle(state)
    assert (state.get('rejudge') or {}).get('status') == 'completed'
    valid = {k: v for k, v in state['scoring_results'].items() if valid_result(v)}
    assert len(valid) == 1011, 'six missing Judge results must be accepted first'
    targets = set(plan['qa_ids'])
    original = {i['qa_id']: i for i in state['items']}
    for receipt in plan['original_attempts']:
        assert reader.reader.fingerprint(original[receipt['qa_id']]) == receipt['item_sha256']
    source_sha = manifest_sha(SOURCE)
    retained_items = [i for i in state['items'] if i['qa_id'] not in targets]
    retained_scores = {k: v for k, v in state['scoring_results'].items() if v['qa_id'] not in targets}
    removed_scores = {k: v for k, v in state['scoring_results'].items() if v['qa_id'] in targets}
    intent = {'source_run_id': SOURCE, 'source_manifest_sha256': source_sha,
        'seed_run_id': SEED, 'qa_ids': plan['qa_ids'], 'inherited_items': len(retained_items),
        'all_original_scores_sha256': reader.reader.fingerprint(state['scoring_results']),
        'inherited_scores_sha256': reader.reader.fingerprint(retained_scores),
        'prior_attempt_scores': removed_scores, 'authorization': plan['authorization']}
    save('anomaly17-seed-intent.json', intent)
    state['items'] = retained_items
    state['scoring_results'] = retained_scores
    state['diagnostic_results'] = {k: v for k, v in (state.get('diagnostic_results') or {}).items()
                                   if v.get('qa_id') not in targets}
    state.pop('rejudge', None)
    state['scoring_summaries'] = summarize_batches(state)
    state.update(run_id=SEED, status='failed', current_phase=None, resumed_from=SOURCE,
        fatal_error='Explicitly authorized separate attempt seed: 17 anomalous answers retained in source run.')
    state['authorized_attempt'] = plan
    result = SplitResultWriter(ROOT).write(ROOT / SEED, state)
    checked = SplitRunReader(ROOT, ROOT / SEED)
    assert len(checked.state()['items']) == 996
    assert checked.reader.fingerprint(checked.state()['scoring_results']) == intent['inherited_scores_sha256']
    assert manifest_sha(SOURCE) == source_sha
    save('anomaly17-seed-response.json', {'seed_run_id': SEED, 'inherited_items': 996,
        'remaining_items': 17, 'original_manifest_unchanged': True, 'write': result})
    print(json.dumps({'seed_run_id': SEED, 'inherited_items': 996, 'remaining_items': 17}))


def resume(wait_id):
    uuid.UUID(wait_id)
    assert (HERE / 'anomaly17-seed-response.json').exists()
    assert not (HERE / 'anomaly17-resume-intent.json').exists()
    _, state = source()
    idle(state)
    plan = api(f'runs/{SEED}/recovery-plan')
    assert plan['can_resume'] and plan['inherited_qa_count'] == 996 and plan['remaining_qa_count'] == 17
    assert not plan['product_version_changed'] and not plan['retryable_service_failures']
    mapping = json.loads((BASE / 'active-runs.json').read_text())
    payload = {'repair_missing_judges': True}
    save('anomaly17-resume-intent.json', {'wait_id': wait_id, 'source_run_id': SOURCE,
        'seed_run_id': SEED, 'recovery_plan': plan, 'payload': payload,
        'new_attempts': json.loads((HERE / 'anomaly17-plan.json').read_text())})
    result = api(f'runs/{SEED}/resume', payload)
    save('anomaly17-resume-response.json', result)
    assert result['resumed_from'] == SEED and result['resumed_items'] == 996
    assert result['scope_id'] == state['scope_id'] and not result['retried_qa_ids']
    assert json.loads((BASE / 'active-runs.json').read_text()) == mapping
    mapping['qwen37'] = result
    temporary = HERE / 'anomaly17-active-runs.next.json'
    temporary.write_text(json.dumps(mapping, indent=2))
    temporary.replace(BASE / 'active-runs.json')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('operation', choices=['plan', 'seed', 'resume'])
    parser.add_argument('--wait-id')
    args = parser.parse_args()
    {'plan': plan, 'seed': seed, 'resume': lambda: resume(args.wait_id)}[args.operation]()
