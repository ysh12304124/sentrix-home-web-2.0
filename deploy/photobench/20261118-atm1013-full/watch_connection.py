"""Wait locally for SSH recovery after a verified outage; leave remote jobs untouched."""
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
while True:
    try:
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=1',
            '192.168.0.100', 'true'], capture_output=True, text=True, timeout=25)
        evidence = {'at': datetime.now().astimezone().isoformat(), 'host': '192.168.0.100',
                    'ssh_reachable': result.returncode == 0, 'returncode': result.returncode,
                    'stderr': result.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        evidence = {'at': datetime.now().astimezone().isoformat(), 'host': '192.168.0.100',
                    'ssh_reachable': False, 'error': 'SSH probe timed out after 25 seconds'}
    (args.audit / 'connectivity-latest.json').write_text(json.dumps(evidence, indent=2))
    if args.check:
        print(json.dumps(evidence))
        sys.exit(0 if evidence['ssh_reachable'] else 1)
    if evidence['ssh_reachable'] or time.monotonic() - started >= 86400:
        reason = '100 SSH connection recovered; inspect both exact runs' if evidence['ssh_reachable'] else '100 SSH still unavailable after a 24-hour recovery watch'
        event = args.audit / f'connectivity-event-{int(time.time())}.json'
        event.write_text(json.dumps({'status': 'needs_review', 'reason': reason, 'evidence': evidence}, indent=2))
        subprocess.run([sys.executable, str(Path.home() / '.codex/skills/wakecodex/scripts/send_event.py'),
                        '--registration', str(args.registration), '--result', str(event)], check=True)
        print(json.dumps({'event': str(event), 'reason': reason}), flush=True)
        break
    time.sleep(60)
