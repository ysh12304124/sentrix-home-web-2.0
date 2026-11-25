"""Update the effective existing systemd override and verify the real app-dir."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
NEW = HERE / 'evaluation/apps/photobench/src'
OLD = BASE / 'full-lazy-18146f7/evaluation/apps/photobench/src'
UNIT = 'sentrix-atm100-doubao-photobench.service'
PROTECTED = ['sentrix-atm100-photobench.service', 'sentrix-atm100-api.service',
             'sentrix-atm100-doubao-api.service', 'sentrix-atm100-model.service',
             'sentrix2-api.service', 'sentrix2-photobench.service']


def api(path, port=8874):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/{path}', timeout=30) as response:
        return json.load(response)


def pid(unit):
    return subprocess.check_output(['systemctl', '--user', 'show', unit, '-p', 'MainPID', '--value'], text=True).strip()


def save(name, value):
    with (HERE / name).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    assert not (HERE / 'activation-fix-intent.json').exists()
    assert not api('active-work')['work']
    scope = 'PhotoBench-20261118-043128-2026.11.17-atm-sources-addresses-current'
    with sqlite3.connect('file:/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db?mode=ro', uri=True) as db:
        assert not db.execute("SELECT id FROM agent_turns WHERE scope_id=? AND status IN ('pending','running')", (scope,)).fetchall()
    imports = api('imports/progress?scope_id=' + scope, 8294)
    assert not imports['library']['processing'] and imports['postprocess']['state'] == 'idle'
    old_pid = pid(UNIT)
    command = Path('/proc/' + old_pid + '/cmdline').read_bytes().decode().split('\0')
    assert command[command.index('--app-dir') + 1] == str(OLD)
    manifest = json.loads((HERE / 'manifest.json').read_text())
    for name, record in manifest['files'].items():
        assert hashlib.sha256((HERE / 'evaluation' / name).read_bytes()).hexdigest() == record['after']
    directory = Path.home() / '.config/systemd/user' / (UNIT + '.d')
    effective = directory / 'zzzzzzzz-full-lazy.conf'
    old_text = effective.read_text()
    assert old_text.count(str(OLD)) == 1
    new_text = old_text.replace(str(OLD), str(NEW))
    before = {unit: pid(unit) for unit in PROTECTED}
    ineffective = {name: (directory / name).read_text() for name in ['98-qwen-live-stats.conf', '99-qwen-committed-list.conf']}
    save('activation-fix-intent.json', {'old_pid': old_pid, 'old_cmdline': command,
                                      'override': str(effective), 'before': old_text, 'after': new_text,
                                      'removed_ineffective_overrides': ineffective, 'protected_pids': before})
    pending = effective.with_suffix('.pending')
    pending.write_text(new_text)
    pending.replace(effective)
    for name in ineffective:
        (directory / name).unlink()
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'restart', UNIT], check=True)
    deadline = time.monotonic() + 60
    while True:
        try:
            assert not api('active-work')['work']
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
    new_pid = pid(UNIT)
    assert new_pid not in {old_pid, '0'}
    argv = Path('/proc/' + new_pid + '/cmdline').read_bytes().decode().split('\0')
    assert argv[argv.index('--app-dir') + 1] == str(NEW)
    assert before == {u: pid(u) for u in PROTECTED}
    loaded_hashes = {name: hashlib.sha256((HERE / 'evaluation' / name).read_bytes()).hexdigest() for name in manifest['files']}
    save('activation-fix-response.json', {'pid': new_pid, 'cmdline': argv,
                                         'actual_app_dir': str(NEW), 'verified_file_sha256': loaded_hashes,
                                         'protected_pids_unchanged': True, 'supersedes': ['activate-response.json', '../qwen-persist-recovery-b0c982f/activate-response.json']})
    print(json.dumps({'pid': new_pid, 'actual_app_dir': str(NEW), 'protected_pids_unchanged': True}), flush=True)


if __name__ == '__main__':
    main()
