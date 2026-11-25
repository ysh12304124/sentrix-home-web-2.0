"""Read exact active checkpoints and product database progress without loading traces."""

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
ROOTS = {
    'q4km': (Path('/home/realmagic/xy/atm-bench-20261110/results'),
             Path('/home/realmagic/xy/atm-bench-20261110/product-data/sentrix.db')),
    'qwen37': (BASE / 'cloud-results',
               Path('/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db')),
}


def inspect():
    mapping = json.loads((BASE / 'active-runs.json').read_text())
    result = {'at': datetime.now().astimezone().isoformat(), 'runs': {},
              'disk_free': {p: shutil.disk_usage(p).free for p in ['/home', '/var/tmp']}}
    for stage, (root, database) in ROOTS.items():
        run_id = mapping[stage]['run_id']
        directory = root / run_id
        pointer = json.loads((directory / 'snapshot-commit.json').read_text())
        metadata = directory / '.snapshots' / pointer['generation'] / 'metadata.json'
        overview = json.loads(metadata.read_text())['overview']
        assert overview['run_id'] == run_id
        row = {key: overview.get(key) for key in [
            'run_id', 'status', 'current_phase', 'item_count', 'qa_count', 'scope_id',
            'fatal_error', 'failed_phase', 'execution_budgets']}
        row['checkpoint_modified'] = datetime.fromtimestamp(metadata.stat().st_mtime).astimezone().isoformat()
        row['phases'] = {name: {k: v for k, v in value.items() if k in {
            'status', 'progress', 'operation_id', 'agent_completed', 'judge_completed', 'error'}}
            for name, value in overview.get('phases', {}).items() if name != 'gpu_metrics'}
        with sqlite3.connect(f'file:{database}?mode=ro', uri=True, timeout=10) as db:
            db.execute('pragma query_only=on')
            scope = overview['scope_id']
            row['turns'] = dict(db.execute(
                'select status,count(*) from agent_turns where scope_id=? group by status', (scope,)))
            columns = [r[1] for r in db.execute('pragma table_info(agent_turns)')]
            fields = [f for f in ['id', 'status', 'created_at', 'started_at', 'updated_at',
                                  'finished_at', 'error', 'step_count'] if f in columns]
            row['recent_turns'] = [dict(zip(fields, r)) for r in db.execute(
                f'select {",".join(fields)} from agent_turns where scope_id=? order by rowid desc limit 8', (scope,))]
            row['pipeline_receipts'] = [list(r) for r in db.execute(
                'select operation_id,kind,status,started_at,finished_at,error from pipeline_receipts '
                'where scope_id=? order by started_at', (scope,))]
            row['postprocess'] = [list(r) for r in db.execute(
                'select dirty_generation,processed_generation,processing_generation,updated_at '
                'from scope_postprocess where scope_id=?', (scope,))]
        result['runs'][stage] = row
    return result


if __name__ == '__main__':
    print(json.dumps(inspect(), ensure_ascii=False, indent=2))
