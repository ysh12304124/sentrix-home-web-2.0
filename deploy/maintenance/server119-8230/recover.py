"""Restore server119 8230 using the existing WeMM and generation services.

Run baseline once before install. No model download or new model process is needed.
No business source, historical turn, or embedding collection is rewritten.
"""
import argparse
import hashlib
import json
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

HOME = Path('/home/asus')
ROOT = HOME / 'xy/audit/20261124-server119-8230-repair'
REPO = HOME / 'Github/sentrix-home-web2.0-exp'
UNITS = HOME / '.config/systemd/user'
PROTECTED = ['sentrix-api', 'sentrix-exp-8772', 'sentrix-https',
             'sentrix-sensevoice', 'sentrix-wemm-sidecar', 'strata-iq3xxs', 'octopus']


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def state():
    with sqlite3.connect(f'file:{REPO}/data-exp/sentrix.db?mode=ro', uri=True) as db:
        turns = db.execute('SELECT * FROM agent_turns ORDER BY id').fetchall()
        turn_sha = hashlib.sha256(json.dumps(turns, ensure_ascii=False).encode()).hexdigest()
        eligible = db.execute("SELECT count(*) FROM agent_turns WHERE status='pending' OR "
                              "(status='running' AND lease_expires_at IS NOT NULL)").fetchone()[0]
        counts = {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                  for table in ['assets', 'text_vectors', 'import_jobs', 'video_jobs']}
    services = {unit: run('systemctl', '--user', 'show', unit + '.service',
                         '-p', 'MainPID', '-p', 'ActiveState', '-p', 'NRestarts')
                for unit in PROTECTED}
    vectors = {p.parent.name: digest(p) for p in (REPO / 'data-exp/qdrant/collection').glob('*/storage.sqlite')}
    return dict(turn_sha=turn_sha, eligible=eligible, counts=counts, services=services, vectors=vectors)


def save(name, data):
    (ROOT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def baseline():
    ROOT.mkdir(parents=True, exist_ok=True)
    assert not (ROOT / 'baseline.json').exists(), 'Baseline already captured'
    before = state()
    assert before['eligible'] == 0, 'Historical work would be resumed'
    save('baseline.json', before)
    (ROOT / 'sentrix-exp-8230.service.original').write_bytes((UNITS / 'sentrix-exp-8230.service').read_bytes())
    (ROOT / 'failure-journal.txt').write_text(run('journalctl', '--user', '-u', 'sentrix-exp-8230', '-n', '100', '--no-pager'))
    print(json.dumps(before, ensure_ascii=False))


def install():
    assert (ROOT / 'baseline.json').exists()
    assert not (ROOT / 'install-started.json').exists(), 'Install already started; inspect current state'
    assert state() == json.loads((ROOT / 'baseline.json').read_text()), 'Baseline changed'
    drop = UNITS / 'sentrix-exp-8230.service.d/90-dependencies.conf'
    assert not drop.exists()
    save('install-started.json', {'time': time.time()})
    drop.parent.mkdir(exist_ok=True)
    drop.write_text('[Service]\nEnvironment=SENTRIX_EMBEDDING_SCHEME=wemm\n'
                    'Environment=SENTRIX_WEMM_URL=http://127.0.0.1:8102\n'
                    'Environment=SENTRIX_VLM_BASE_URL=http://127.0.0.1:8081/v1\n'
                    'Environment=SENTRIX_VLM_MODEL=qwen3.8-flash-next-iq3_xxs\n'
                    'Environment=SENTRIX_SPEECH_SIDECAR_URL=http://127.0.0.1:8107\n'
                    'Restart=on-failure\nRestartSec=30\n')
    run('systemctl', '--user', 'daemon-reload')
    print('8230 override installed; existing model services reused. Start 8230 explicitly.')


def verify():
    before = json.loads((ROOT / 'baseline.json').read_text())
    after = state()
    health = {}
    for port, path in [(8102, '/health'), (8230, '/api/v1/diagnostics')]:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=30) as response:
            health[str(port)] = json.load(response)
    result = dict(unchanged=before == after, before=before, after=after, health=health,
                  service=run('systemctl', '--user', 'show', 'sentrix-exp-8230.service',
                              '-p', 'MainPID', '-p', 'ActiveState', '-p', 'NRestarts'))
    save('verification.json', result)
    print(json.dumps(result, ensure_ascii=False))
    assert result['unchanged'], 'Review database or protected-service differences'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['baseline', 'install', 'verify'])
    args = parser.parse_args()
    {'baseline': baseline, 'install': install, 'verify': verify}[args.mode]()
