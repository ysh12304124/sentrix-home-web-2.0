"""Read the exact scoring auxiliary activity and a compact checkpoint."""
import argparse
import json
import shutil
import urllib.request
from pathlib import Path

from photobench.infrastructure.results.split_store import SplitRunReader

parser = argparse.ArgumentParser(__doc__)
parser.add_argument('task_id')
args = parser.parse_args()
base = Path('/home/realmagic/xy/experiments/atm1013-20261118')
run = '20261119-213836-2026.11.17-atm-sources-addresses-current-resume-0e3686'
assert json.loads((base / 'active-runs.json').read_text())['qwen37']['run_id'] == run
root = base / 'cloud-results'
overview = SplitRunReader(root, root / run).overview()
with urllib.request.urlopen('http://127.0.0.1:8874/api/v1/active-work', timeout=15) as response:
    work = json.load(response)['work']
activity = overview.get('rejudge') or {}
assert activity['rejudge_id'] == args.task_id
print(json.dumps({'run_id': run, 'rejudge_id': args.task_id,
                  'status': activity['status'], 'completed': activity['completed'],
                  'total': activity['total'], 'failed': activity['failed'], 'error': activity.get('error'),
                  'worker_alive': any(r['id'] == args.task_id and r['worker_alive'] for r in work),
                  'scoring': overview['summary']['scoring']['profiles']['atm_qs_v1'],
                  'disk_free': {p: shutil.disk_usage(p).free for p in ['/home', '/var/tmp']}}))
