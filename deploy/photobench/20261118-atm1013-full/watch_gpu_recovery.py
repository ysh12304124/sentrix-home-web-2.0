"""Wait locally for GPU recovery; report evidence without restarting services or runs."""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

parser = argparse.ArgumentParser(__doc__)
parser.add_argument('--registration', type=Path)
parser.add_argument('--audit', type=Path, required=True)
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
assert args.check or args.registration is not None
started = time.monotonic()
failures = 0
while True:
    reason = ''
    try:
        response = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=1', '192.168.0.100',
            '/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python',
            '/home/realmagic/xy/experiments/atm1013-20261118/inspect_reboot.py', '--gpu-only'],
            capture_output=True, text=True, timeout=45, check=True)
        snapshot = json.loads(response.stdout)
        failures = 0
        if snapshot['gpu_ready']:
            reason = '100 GPUs recovered; inspect services and resume the two original checkpoints'
    except (subprocess.SubprocessError, ValueError, KeyError) as error:
        failures += 1
        snapshot = {'at': datetime.now().astimezone().isoformat(), 'gpu_ready': False,
                    'probe_error': str(error), 'consecutive_failures': failures}
        if failures >= 3:
            reason = 'Three consecutive GPU recovery probes failed; inspect SSH and probe execution'
    (args.audit / 'gpu-recovery-latest.json').write_text(json.dumps(snapshot, indent=2) + '\n')
    if args.check:
        print(json.dumps(snapshot))
        sys.exit(0 if 'probe_error' not in snapshot else 1)
    if not reason and time.monotonic() - started >= 86400:
        reason = '100 GPU recovery still requires administrator action after 24 hours'
    if reason:
        event = args.audit / f'gpu-recovery-event-{int(time.time())}.json'
        event.write_text(json.dumps({'status': 'needs_review', 'reason': reason,
                                     'snapshot': snapshot}, indent=2) + '\n')
        subprocess.run([sys.executable, str(Path.home() / '.codex/skills/wakecodex/scripts/send_event.py'),
                        '--registration', str(args.registration), '--result', str(event)], check=True)
        print(json.dumps({'event': str(event), 'reason': reason}), flush=True)
        break
    time.sleep(60)
