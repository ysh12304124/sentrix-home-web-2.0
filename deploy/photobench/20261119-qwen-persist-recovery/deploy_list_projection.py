"""Single-use follow-up candidate; activate after the three-question run is idle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
OLD = BASE / 'qwen-persist-recovery-b0c982f/evaluation'
NEW = HERE / 'evaluation'
PYTHON = '/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python'
RUN = '20261119-213836-2026.11.17-atm-sources-addresses-current-resume-0e3686'
SCOPE = 'PhotoBench-20261118-043128-2026.11.17-atm-sources-addresses-current'
UNIT = 'sentrix-atm100-doubao-photobench.service'
PROTECTED = ['sentrix-atm100-photobench.service', 'sentrix-atm100-api.service',
             'sentrix-atm100-doubao-api.service', 'sentrix-atm100-model.service',
             'sentrix2-api.service', 'sentrix2-photobench.service']


def save(name, value):
    with (HERE / name).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def api(path, port=8874):
    started = time.monotonic()
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/{path}', timeout=30) as response:
        raw = response.read()
    return json.loads(raw), {'seconds': time.monotonic() - started, 'bytes': len(raw)}


def pid(unit):
    return subprocess.check_output(['systemctl', '--user', 'show', unit, '-p', 'MainPID', '--value'], text=True).strip()


def stage():
    assert not NEW.exists() and not (HERE / 'stage-intent.json').exists()
    info = json.loads((HERE / 'manifest.json').read_text())
    for path, hashes in info['files'].items():
        assert sha(OLD / path) == hashes['before'], path
        assert sha(HERE / 'payload' / path) == hashes['after'], path
    save('stage-intent.json', info)
    shutil.copytree(OLD, NEW, symlinks=True, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    for path in info['files']:
        shutil.copyfile(HERE / 'payload' / path, NEW / path)
    env = {**os.environ, 'PYTHONPATH': str(NEW / 'apps/photobench/src')}
    with (HERE / 'candidate-tests.log').open('x') as stream:
        subprocess.run([PYTHON, '-m', 'pytest', '-o', 'addopts=', '-q', 'apps/photobench/tests/test_suites.py',
                        'apps/photobench/tests/test_split_results.py', 'apps/photobench/tests/test_run_read.py'],
                       cwd=NEW, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
    save('stage-response.json', {'revision': info['revision'], 'tests': (HERE / 'candidate-tests.log').read_text()[-1500:]})


def activate():
    assert (HERE / 'stage-response.json').is_file() and (HERE / 'live-write-response.json').is_file()
    assert not (HERE / 'activate-intent.json').exists()
    mapping = json.loads((BASE / 'active-runs.json').read_text())
    assert mapping['qwen37']['run_id'] == RUN
    overview, _ = api(f'runs/{RUN}?include_history=false')
    assert overview['status'] in {'completed', 'completed_with_errors', 'failed', 'interrupted'}
    assert not api('active-work')[0]['work']
    with sqlite3.connect('file:/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db?mode=ro', uri=True) as db:
        assert not db.execute("SELECT id FROM agent_turns WHERE scope_id=? AND status IN ('pending','running')", (SCOPE,)).fetchall()
    progress, _ = api('imports/progress?scope_id=' + SCOPE, 8294)
    assert progress['postprocess']['state'] == 'idle' and not progress['library']['processing']
    before = {u: pid(u) for u in PROTECTED}
    old_pid = pid(UNIT)
    save('activate-intent.json', {'run_id': RUN, 'status': overview['status'], 'protected_pids': before, 'old_pid': old_pid})
    override = Path.home() / '.config/systemd/user' / (UNIT + '.d') / '99-qwen-committed-list.conf'
    assert not override.exists()
    override.write_text('[Service]\nExecStart=\nExecStart=' + PYTHON +
                        ' -m uvicorn --factory photobench.main:create_app --host 0.0.0.0 --port 8874 --app-dir ' +
                        str(NEW / 'apps/photobench/src') + '\n')
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'restart', UNIT], check=True)
    deadline = time.monotonic() + 60
    while True:
        try:
            assert not api('active-work')[0]['work']
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
    assert pid(UNIT) not in {old_pid, '0'} and before == {u: pid(u) for u in PROTECTED}
    _, timing = api('runs')
    save('activate-response.json', {'pid': pid(UNIT), 'protected_pids_unchanged': True, 'run_id': RUN,
                                   'list_timing': timing, 'app_dir': str(NEW / 'apps/photobench/src')})
    print(json.dumps({'pid': pid(UNIT), 'list_timing': timing}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('operation', choices=['stage', 'activate'])
    {'stage': stage, 'activate': activate}[parser.parse_args().operation]()
