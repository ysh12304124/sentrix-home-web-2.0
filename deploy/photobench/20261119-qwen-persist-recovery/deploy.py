"""Single-use evaluator candidate and idle-only activation on port 8874."""
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
OLD = BASE / 'full-lazy-18146f7/evaluation'
NEW = HERE / 'evaluation'
PYTHON = '/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python'
UNIT = 'sentrix-atm100-doubao-photobench.service'
SOURCE = '20261119-205809-2026.11.17-atm-sources-addresses-current-resume-4c5f90'
SCOPE = 'PhotoBench-20261118-043128-2026.11.17-atm-sources-addresses-current'
PROTECTED = ['sentrix-atm100-photobench.service', 'sentrix-atm100-api.service',
             'sentrix-atm100-doubao-api.service', 'sentrix-atm100-model.service',
             'sentrix2-api.service', 'sentrix2-photobench.service']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    with (HERE / name).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def api(path, port=8874):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/{path}', timeout=45) as response:
        return json.load(response)


def pid(unit):
    return subprocess.check_output(['systemctl', '--user', 'show', unit, '-p', 'MainPID', '--value'], text=True).strip()


def idle():
    assert json.loads((BASE / 'active-runs.json').read_text())['qwen37']['run_id'] == SOURCE
    assert not api('active-work')['work']
    with sqlite3.connect('file:/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db?mode=ro', uri=True) as db:
        assert not db.execute("SELECT id FROM agent_turns WHERE scope_id=? AND status IN ('pending','running')", (SCOPE,)).fetchall()
    progress = api('imports/progress?scope_id=' + SCOPE, 8294)
    assert progress['postprocess']['state'] == 'idle' and not progress['library']['processing']


def stage():
    assert not NEW.exists() and not (HERE / 'stage-intent.json').exists()
    info = json.loads((HERE / 'manifest.json').read_text())
    for path, hashes in info['files'].items():
        assert sha(OLD / path) == hashes['before'], path + ' baseline changed'
        assert sha(HERE / 'payload' / path) == hashes['after']
    save('stage-intent.json', info)
    shutil.copytree(OLD, NEW, symlinks=True, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    for path in info['files']:
        target = NEW / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / 'payload' / path, target)
    env = {**os.environ, 'PYTHONPATH': str(NEW / 'apps/photobench/src')}
    with (HERE / 'candidate-tests.log').open('x') as stream:
        subprocess.run([PYTHON, '-m', 'pytest', '-o', 'addopts=', '-q',
                        'apps/photobench/tests/test_suites.py',
                        'apps/photobench/tests/test_split_results.py',
                        'apps/photobench/tests/test_run_execution.py'], cwd=NEW, env=env,
                       stdout=stream, stderr=subprocess.STDOUT, check=True)
    save('stage-response.json', {'revision': info['revision'], 'tests': (HERE / 'candidate-tests.log').read_text()[-1500:]})


def review_tests():
    """Compare the five endpoint-adapter failures with the unmodified deployed base."""
    import xml.etree.ElementTree as ET

    assert NEW.exists() and not (HERE / 'stage-response.json').exists()
    names = [
        'test_explicit_current_model_reuse_never_calls_manager_start_or_stop',
        'test_with_a_manager_the_model_is_stopped_then_started_then_probed',
        'test_the_managed_branch_also_points_sentrix_at_the_new_model',
        'test_a_transient_start_failure_is_retried_before_giving_up',
        'test_reuse_synchronizes_product_without_touching_model_lifecycle',
    ]
    results = {}
    for label, directory in [('baseline', OLD), ('candidate', NEW)]:
        xml = HERE / (label + '-review.xml')
        assert not xml.exists()
        env = {**os.environ, 'PYTHONPATH': str(directory / 'apps/photobench/src')}
        with (HERE / (label + '-review.log')).open('x') as stream:
            command = [PYTHON, '-m', 'pytest', '-o', 'addopts=', '-q', '--junitxml=' + str(xml)]
            if label == 'baseline':
                command += ['apps/photobench/tests/test_run_execution.py::' + name for name in names]
            else:
                command += ['apps/photobench/tests/test_suites.py', 'apps/photobench/tests/test_split_results.py',
                            'apps/photobench/tests/test_run_execution.py']
            completed = subprocess.run(command, cwd=directory, env=env, stdout=stream, stderr=subprocess.STDOUT)
        assert completed.returncode == 1
        tests = ET.parse(xml).getroot().findall('.//testcase')
        failed = sorted(t.attrib['name'] for t in tests if t.find('failure') is not None)
        assert failed == sorted(names) and not any(t.find('error') is not None for t in tests)
        results[label] = {'failures': failed, 'passed': len(tests) - len(failed), 'junit': str(xml)}
    assert results['candidate']['passed'] == 209
    info = json.loads((HERE / 'manifest.json').read_text())
    save('stage-response.json', {'revision': info['revision'], 'tests': results,
                                 'new_regressions': 0, 'baseline_failures_retained': True})
    print(json.dumps(results), flush=True)


def activate():
    assert (HERE / 'stage-response.json').is_file() and (HERE / 'restore-response.json').is_file()
    assert not (HERE / 'activate-intent.json').exists()
    idle()
    info = json.loads((HERE / 'manifest.json').read_text())
    for path, hashes in info['files'].items():
        assert sha(NEW / path) == hashes['after']
    before = {u: pid(u) for u in PROTECTED}
    old_pid = pid(UNIT)
    pointer = BASE / 'cloud-results' / SOURCE / 'split-commit.json'
    source_sha = sha(pointer)
    save('activate-intent.json', {'revision': info['revision'], 'unit': UNIT, 'old_pid': old_pid,
                                 'protected_pids': before, 'source_manifest_sha256': source_sha})
    override = Path.home() / '.config/systemd/user' / (UNIT + '.d') / '98-qwen-live-stats.conf'
    assert not override.exists()
    override.write_text('[Service]\nExecStart=\nExecStart=' + PYTHON +
                        ' -m uvicorn --factory photobench.main:create_app --host 0.0.0.0 --port 8874 --app-dir ' +
                        str(NEW / 'apps/photobench/src') + '\n')
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'restart', UNIT], check=True)
    deadline = time.monotonic() + 60
    while True:
        try:
            idle()
            break
        except (OSError, ValueError):
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
    assert pid(UNIT) != old_pid and pid(UNIT) != '0'
    after = {u: pid(u) for u in PROTECTED}
    assert before == after and sha(pointer) == source_sha
    save('activate-response.json', {'revision': info['revision'], 'unit': UNIT, 'pid': pid(UNIT),
                                   'protected_pids': after, 'source_manifest_unchanged': True,
                                   'app_dir': str(NEW / 'apps/photobench/src'), 'idle_verified': True})
    print(json.dumps({'pid': pid(UNIT), 'protected_pids_unchanged': True}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('operation', choices=['stage', 'review-tests', 'activate'])
    {'stage': stage, 'review-tests': review_tests, 'activate': activate}[parser.parse_args().operation]()
