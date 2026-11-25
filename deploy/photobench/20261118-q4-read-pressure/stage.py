"""Prepare a read-optimization candidate without changing running services."""

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
OLD = BASE / 'q4-checkpoint-release'
NEW = BASE / 'q4-read-release'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


manifest = json.loads((HERE / 'manifest.json').read_text())
assert not NEW.exists(), 'Inspect the existing candidate instead of repeating staging'
for name, row in manifest['files'].items():
    assert sha(OLD / name) == row['before'], name
    assert sha(HERE / 'overlay' / name) == row['after'], name
units = ['sentrix-atm100-photobench.service', 'sentrix-atm100-doubao-photobench.service',
         'sentrix-atm100-api.service', 'sentrix-atm100-doubao-api.service',
         'sentrix2-api.service', 'sentrix2-photobench.service']


def pids():
    return {unit: subprocess.check_output(
        ['systemctl', '--user', 'show', unit, '-p', 'MainPID', '--value'], text=True).strip()
        for unit in units}


before = pids()
intent = {'at': time.time(), 'manifest': manifest, 'protected_pids': before}
with (HERE / 'stage-intent.json').open('x') as handle:
    json.dump(intent, handle, indent=2)
shutil.copytree(OLD, NEW, symlinks=True, ignore=shutil.ignore_patterns('__pycache__'))
for name, row in manifest['files'].items():
    shutil.copy2(HERE / 'overlay' / name, NEW / name)
    assert sha(NEW / name) == row['after'], name
assert pids() == before
result = {'candidate': str(NEW), 'active_release': str(OLD), 'activated': False,
          'source_sha_verified': True, 'protected_pids': before}
with (HERE / 'stage-response.json').open('x') as handle:
    json.dump(result, handle, indent=2)
print(json.dumps(result))
