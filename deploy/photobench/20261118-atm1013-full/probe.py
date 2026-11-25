"""Read compact committed metadata and live worker state for one recorded run."""
import argparse
import json
import shutil
import sqlite3
import urllib.request
from pathlib import Path

from launch import STAGES
from prepare_runtime import BASE, DATA, api


def probe(stage, allow_active_timeout=False):
    port, product, endpoint, model = STAGES[stage]
    mapping_path = BASE / 'active-runs.json'
    mapping = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
    run_id = (mapping[stage]['run_id'] if stage in mapping else
              json.loads((BASE / f'{stage}-launch-response.json').read_text())['run_ids'][0])
    root = Path('/home/realmagic/xy/atm-bench-20261110/results') if stage == 'q4km' else BASE / 'cloud-results'
    run_dir = root / run_id
    split = run_dir / 'split-commit.json'
    if split.is_file():
        state = json.loads(split.read_text())['monitor']
    else:
        pointer = json.loads((run_dir / 'snapshot-commit.json').read_text())
        metadata = run_dir / '.snapshots' / pointer['generation'] / 'metadata.json'
        state = json.loads(metadata.read_text())['overview']
    assert state['run_id'] == run_id
    keys = ['run_id', 'status', 'current_phase', 'scope_id', 'qa_count', 'item_count', 'reasoning',
            'graph_enabled', 'model_name', 'model_identity', 'runtime_snapshot', 'execution_budgets',
            'failed_phase', 'fatal_error', 'started_at', 'finished_at', 'summary', 'scope_build']
    result = {k: state.get(k) for k in keys}
    result['stage'] = stage
    fields = {'status', 'progress', 'agent_completed', 'agent_total', 'judge_completed', 'judge_total',
              'started_at', 'finished_at', 'error', 'operation_id', 'imported', 'failed', 'total'}
    result['phases'] = {name: {k: v for k, v in phase.items() if k in fields}
                        for name, phase in state.get('phases', {}).items() if name != 'gpu_metrics'}
    result['deployment'] = {k: state.get('phases', {}).get('model_deploy', {}).get(k)
                            for k in ['model_identity', 'gamma_sync']}
    if allow_active_timeout:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/active-work', timeout=5) as response:
                result['active'] = json.load(response)['work']
        except TimeoutError as error:
            result['active'] = None
            result['active_probe_error'] = f'{type(error).__name__}: {error}'
            result['worker_liveness'] = 'unverified_status_api_timeout'
        with sqlite3.connect(f'file:{DATA[product]}/sentrix.db?mode=ro', uri=True, timeout=10) as db:
            db.execute('pragma query_only=on')
            result['live_turns'] = [list(row) for row in db.execute(
                'select t.id,t.status,coalesce(max(e.event_seq),0) from agent_turns t '
                'left join agent_turn_events_v2 e on e.turn_id=t.id '
                'where t.scope_id=? and t.status in (\'pending\',\'running\') group by t.id,t.status',
                (state['scope_id'],))]
    else:
        result['active'] = api(port, 'active-work')['work']
    result['imports'] = api(product, 'imports/progress?scope_id=' + state['scope_id'])
    result['disk_free'] = {p: shutil.disk_usage(p).free for p in ['/home', '/var/tmp']}
    with urllib.request.urlopen(f'http://127.0.0.1:{endpoint}/v1/models', timeout=15) as response:
        discovered = json.load(response)['data'][0]['id']
    result['model_matches'] = discovered == model
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('stage', choices=STAGES)
    parser.add_argument('--allow-active-timeout', action='store_true')
    args = parser.parse_args()
    print(json.dumps(probe(args.stage, args.allow_active_timeout)))
