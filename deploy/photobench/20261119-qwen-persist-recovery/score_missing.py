"""Judge six missing ATM results from saved answers in the original batch."""
from __future__ import annotations

import argparse
import json
import urllib.request
import uuid
from pathlib import Path

from photobench.domain.evaluation.profiles import scoring_targets, valid_result
from photobench.infrastructure.results.split_store import SplitRunReader

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
ROOT = BASE / 'cloud-results'
HERE = Path(__file__).resolve().parent
RUN = '20261119-213836-2026.11.17-atm-sources-addresses-current-resume-0e3686'
BATCH = 'score-dd0413c8aa869b1c84626de7'
QA = ['atm-71582c85-dbc5-4460-b549-66d323ec362a', 'atm-bfe15c10-8ee3-41ed-87a8-c134dbd196ec',
      'atm-1e644e18-6645-43fa-bb0c-156d88692427', 'atm-b767821e-3d20-4880-8de0-81c6bedf1577',
      'atm-3aff6eaa-f88c-4301-bca8-cd9643331cfb', 'atm-13ec73ea-7213-4877-ab16-17211ac39982']
FAILURES = {'atm-e388993e-fac9-44a2-a83d-d6ee44be5ee8', 'atm-bd03c436-4698-4baa-8a89-6ae11a72efc9'}


def api(path, data=None):
    request = urllib.request.Request('http://127.0.0.1:8874/api/v1/' + path,
                                    data=None if data is None else json.dumps(data).encode(),
                                    headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def save(name, value):
    with (HERE / name).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def inspect():
    assert json.loads((BASE / 'active-runs.json').read_text())['qwen37']['run_id'] == RUN
    assert not api('active-work')['work']
    reader = SplitRunReader(ROOT, ROOT / RUN)
    state = reader.state()
    assert state['status'] == 'completed_with_errors' and len(state['items']) == state['qa_count'] == 1013
    assert len({i['qa_id'] for i in state['items']}) == 1013
    assert {i['qa_id'] for i in state['items'] if i['execution_status'] != 'completed'} == FAILURES
    batch = next(b for b in state['scoring_batches'] if b['batch_id'] == BATCH)
    assert batch['profile_id'] == 'atm_qs_v1' and batch['rule_version'] == 'scoring-2026.11.12-v5'
    assert batch['judge']['model'] == 'doubao-seed-2.1-lite'
    assert not batch['config']['use_gt_images'] and not batch['config']['use_gt_metadata']
    targets = scoring_targets(state, batch, 'missing')
    assert len(targets) == 6 and {t['qa_id'] for t in targets} == set(QA)
    assert not any(t.get('diagnostics_only') for t in targets)
    valid = {k: v for k, v in state['scoring_results'].items() if valid_result(v)}
    assert len(valid) == 1005
    payload = {'resume_batch_ids': [BATCH], 'scope': 'missing', 'qa_ids': QA}
    preview = api(f'runs/{RUN}/scoring-preview', payload)
    assert preview['qa_count'] == preview['subscore_count'] == 6 and not preview['diagnostic_only_count']
    save('scoring-plan.json', {'run_id': RUN, 'source_revision': reader.revision,
                              'valid_scores_before': 1005, 'valid_score_keys': list(valid),
                              'valid_score_receipts_sha256': reader.reader.fingerprint(valid),
                              'targets': targets, 'preview': preview, 'payload': payload,
                              'retained_execution_failures': sorted(FAILURES), 'agent_rerun': False})
    print(json.dumps({'run_id': RUN, 'valid_scores': 1005, 'judge_targets': 6, 'execution_failures': 2}), flush=True)


def launch(wait_id):
    uuid.UUID(wait_id)
    assert not (HERE / 'scoring-intent.json').exists()
    plan = json.loads((HERE / 'scoring-plan.json').read_text())
    assert not api('active-work')['work']
    current = SplitRunReader(ROOT, ROOT / RUN)
    assert current.revision == plan['source_revision']
    save('scoring-intent.json', {'wait_id': wait_id, **plan})
    response = api(f'runs/{RUN}/rejudge', plan['payload'])
    save('scoring-response.json', response)
    assert response['run_id'] == RUN and response['total'] == 6 and response['status'] == 'running'
    print(json.dumps(response), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('operation', choices=['inspect', 'launch'])
    parser.add_argument('--wait-id')
    args = parser.parse_args()
    inspect() if args.operation == 'inspect' else launch(args.wait_id)
