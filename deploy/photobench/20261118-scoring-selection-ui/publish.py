"""Publish committed scoring UI files without restarting active evaluations."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request


BASE = Path(__file__).resolve().parent
UNITS = [
    'sentrix2-api.service', 'sentrix2-photobench.service',
    'sentrix-atm100-api.service', 'sentrix-atm100-photobench.service',
    'sentrix-atm100-doubao-api.service', 'sentrix-atm100-doubao-photobench.service',
]
RUN_MAPPING = Path('/home/realmagic/xy/experiments/atm1013-20261118/active-runs.json')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def get(port, path):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/{path}', timeout=60) as response:
        return json.load(response)


def save(name, value):
    (BASE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def inspect(manifest):
    pids = subprocess.check_output(
        ['systemctl', '--user', 'show', *UNITS, '-p', 'Id', '-p', 'MainPID'], text=True
    )
    runs = {}
    mapping = json.loads(RUN_MAPPING.read_text())
    for stage, port in [('q4km', 8872), ('qwen37', 8874)]:
        run_id = mapping[stage]['run_id']
        run = get(port, f'runs/{run_id}?include_history=false')
        frozen = {key: run.get(key) for key in [
            'run_id', 'scope_id', 'qa_set', 'qa_count', 'reasoning', 'graph_enabled',
            'scoring_profiles', 'scoring_configs', 'judge_model', 'execution_budgets',
        ]}
        runs[stage] = {'run_id': run_id, 'status': run.get('status'),
                       'phase': run.get('current_phase'), 'profiles': run.get('scoring_profiles'),
                       'frozen_sha256': hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()}
    return {'pids': pids, 'runs': runs, 'files': {
        row['target']: sha(row['target']) if Path(row['target']).exists() else None
        for row in manifest['files']
    }}


def main():
    manifest = json.loads((BASE / 'manifest.json').read_text())
    before = inspect(manifest)
    if sys.argv[1] == 'inspect':
        save('inspection.json', before)
        print(json.dumps(before))
        return
    assert sys.argv[1] == 'install'
    assert not (BASE / 'intent.json').exists(), 'Inspect the existing receipt before continuing.'
    for row in manifest['files']:
        assert before['files'][row['target']] == row['before'], row['target']
        assert sha(BASE / 'payload' / row['source']) == row['after'], row['source']
    save('before.json', before)
    save('intent.json', {'source_revision': manifest['source_revision']})
    for row in sorted(manifest['files'], key=lambda item: item['target'].endswith('/dist/index.html')):
        target = Path(row['target'])
        if target.exists():
            backup = BASE / 'backup' / target.relative_to('/')
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(target.name + '.scoring-ui-update')
        shutil.copy2(BASE / 'payload' / row['source'], staged)
        staged.replace(target)
        assert sha(target) == row['after']
    after = inspect(manifest)
    save('after.json', after)
    assert after['pids'] == before['pids']
    for stage, row in before['runs'].items():
        assert after['runs'][stage]['frozen_sha256'] == row['frozen_sha256'], stage
    receipt = {'status': 'passed', 'source_revision': manifest['source_revision'],
               'file_count': len(manifest['files']), 'services_restarted': False,
               'pids_preserved': True, 'run_scoring_preserved': True}
    save('acceptance.json', receipt)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
