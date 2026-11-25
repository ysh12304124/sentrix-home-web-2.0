"""Read the two original ATM1013 checkpoints and GPU state without changing jobs."""
import argparse
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
STAGES = {
    'q4km': ('20261118-043111-2026.11.17-atm-sources-addresses-current-e53f3c',
             Path('/home/realmagic/xy/atm-bench-20261110/results'),
             Path('/home/realmagic/xy/atm-bench-20261110/product-data/sentrix.db')),
    'qwen37': ('20261118-043128-2026.11.17-atm-sources-addresses-current-7fcbea',
               BASE / 'cloud-results',
               Path('/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db')),
}
GPU1 = 'GPU-4ef9200c-d8f3-638a-3eeb-26fe4fa3d7b0'


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    return {'returncode': result.returncode, 'stdout': result.stdout[-3000:],
            'stderr': result.stderr[-1000:]}


def gpu_snapshot():
    result = command('nvidia-smi', '--query-gpu=uuid,name,driver_version', '--format=csv,noheader')
    uuids = [line.split(',')[0].strip() for line in result['stdout'].splitlines()]
    return {'at': datetime.now().astimezone().isoformat(),
            'kernel': command('uname', '-r')['stdout'].strip(),
            'gpu_ready': result['returncode'] == 0 and len(uuids) == 2 and GPU1 in uuids,
            'nvidia_smi': result}


def scope_snapshot(stage):
    run_id, root, db_path = STAGES[stage]
    run_dir = root / run_id
    pointer = json.loads((run_dir / 'snapshot-commit.json').read_text())
    overview = json.loads((run_dir / '.snapshots' / pointer['generation'] / 'metadata.json').read_text())['overview']
    assert overview['run_id'] == run_id
    fields = ['run_id', 'status', 'current_phase', 'scope_id', 'qa_count', 'item_count',
              'started_at', 'finished_at', 'execution_budgets']
    result = {key: overview.get(key) for key in fields}
    result['generation'] = pointer['generation']
    result['pipeline_asset_count'] = len(overview.get('pipeline_asset_ids', []))
    result['phases'] = {name: {key: phase.get(key) for key in ['status', 'progress', 'operation_id']
                              if key in phase}
                        for name, phase in overview.get('phases', {}).items() if name != 'gpu_metrics'}
    scope = overview['scope_id']
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=10) as db:
        db.execute('pragma query_only=on')
        result['assets'] = [list(row) for row in db.execute(
            'select media_type,status,count(*) from assets where scope_id=? group by media_type,status', (scope,))]
        result['agent_turns'] = dict(db.execute(
            'select status,count(*) from agent_turns where scope_id=? group by status', (scope,)))
        result['video_jobs'] = dict(db.execute(
            'select v.status,count(*) from video_jobs v join assets a on a.id=v.asset_id '
            'where a.scope_id=? group by v.status', (scope,)))
        result['pipeline_receipts'] = [list(row) for row in db.execute(
            'select operation_id,kind,status,started_at,finished_at,error from pipeline_receipts '
            'where scope_id=? order by started_at', (scope,))]
        result['postprocess'] = [list(row) for row in db.execute(
            'select dirty_generation,processed_generation,processing_generation,updated_at '
            'from scope_postprocess where scope_id=?', (scope,))]
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--gpu-only', action='store_true')
    args = parser.parse_args()
    result = gpu_snapshot()
    if not args.gpu_only:
        result['runs'] = {stage: scope_snapshot(stage) for stage in STAGES}
        result['disk_free'] = {path: shutil.disk_usage(path).free for path in ['/home', '/var/tmp']}
        result['services'] = command('systemctl', '--user', 'show',
            'sentrix-atm100-api.service', 'sentrix-atm100-doubao-api.service',
            'sentrix-atm100-wemm.service', 'sentrix-atm100-model.service',
            '-p', 'Id', '-p', 'ActiveState', '-p', 'SubState', '-p', 'MainPID', '-p', 'NRestarts')
    print(json.dumps(result))
