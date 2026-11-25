"""Python-only exact auxiliary monitor; a terminal run alone does not trigger it."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '20261118-atm1013-full'))
from monitor_event import emit_event

parser = argparse.ArgumentParser(__doc__)
parser.add_argument('--task-id', required=True)
parser.add_argument('--registration', type=Path, required=True)
parser.add_argument('--audit', type=Path, required=True)
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
failures = 0
started = time.monotonic()
while True:
    reason = ''
    try:
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '192.168.0.100',
                   'env', 'PYTHONPATH=/home/realmagic/xy/experiments/atm1013-20261118/qwen-live-list-8b6da36/evaluation/apps/photobench/src',
                   '/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python',
                   '/home/realmagic/xy/experiments/atm1013-20261118/qwen-live-list-8b6da36/probe_scoring.py', args.task_id]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=True)
        snapshot = json.loads(completed.stdout)
        failures = 0
        if snapshot['status'] != 'running' and not snapshot['worker_alive']:
            reason = 'Exact scoring activity reached terminal state; verify scores and complete exports'
        elif not snapshot['worker_alive'] and time.monotonic() - started > 120:
            reason = 'Exact scoring auxiliary worker is absent'
        elif min(snapshot['disk_free'].values()) < 1536 * 1024**2:
            reason = 'Disk free space below 1.5 GiB'
        elif time.monotonic() - started > 6 * 3600:
            reason = 'Scoring auxiliary exceeded six-hour monitoring window'
    except Exception as error:
        failures += 1
        snapshot = {'rejudge_id': args.task_id, 'consecutive_failures': failures, 'error': str(error)}
        if failures >= 3:
            reason = 'Three consecutive exact scoring probes failed'
    evidence = args.audit / 'scoring-latest.json'
    evidence.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    if args.check:
        print(json.dumps({'snapshot': snapshot, 'reason': reason}))
        sys.exit(1 if failures else 0)
    if reason:
        event = args.audit / ('scoring-event-' + str(int(time.time())) + '.json')
        event.write_text(json.dumps({'status': 'needs_review', 'reason': reason, 'snapshot': snapshot,
                                     'evidence': str(evidence)}, ensure_ascii=False, indent=2))
        emit_event(args.registration, event)
        break
    time.sleep(60)
