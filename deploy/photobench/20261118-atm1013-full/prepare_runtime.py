"""Prepare the authorized ATM1013 runtime; each mutation has a single-use receipt."""
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
DATA = {8291: Path('/home/realmagic/xy/atm-bench-20261110/product-data'),
        8294: Path('/var/tmp/sentrix-atm-doubao-20261112/product-data')}


def call(*args):
    return subprocess.check_output(args, text=True).strip()


def api(port, path):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/{path}', timeout=30) as response:
        return json.load(response)


def save(name, value):
    (BASE / name).write_text(json.dumps(value, indent=2) + '\n')


def inspect():
    result = {'products': {}, 'evaluators': {}, 'disk': {}}
    for port, root in DATA.items():
        with sqlite3.connect(f'file:{root}/sentrix.db?mode=ro', uri=True) as db:
            rows = {}
            for table in ['agent_turns', 'import_jobs', 'video_jobs', 'pipeline_receipts']:
                columns = [r[1] for r in db.execute(f'pragma table_info({table})')]
                rows[table] = dict(db.execute(f'select status,count(*) from {table} group by status')) if 'status' in columns else {'columns': columns}
            rows['postprocess_running'] = db.execute('select count(*) from scope_postprocess where processing_generation is not null').fetchone()[0]
        rows['imports'] = api(port, 'imports/progress')
        result['products'][str(port)] = rows
    for port in [8872, 8874]:
        result['evaluators'][str(port)] = api(port, 'active-work')
    for path in ['/home', '/var/tmp']:
        result['disk'][path] = shutil.disk_usage(path)._asdict()
    return result


def require_idle(snapshot):
    for rows in snapshot['products'].values():
        for table in ['agent_turns', 'import_jobs', 'video_jobs', 'pipeline_receipts']:
            counts = rows[table]
            active = {'running', 'processing', 'queued', 'indexing', 'extracting'}
            assert not any(counts.get(k) for k in active), (table, counts)
        assert rows['postprocess_running'] == 0, rows
    assert all(not r['work'] for r in snapshot['evaluators'].values())


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_file():
            with path.open('rb') as handle:
                digest = hashlib.file_digest(handle, 'sha256').hexdigest()
            result[str(path.relative_to(root))] = [path.stat().st_size, digest]
    return result


def move_results():
    receipt = BASE / 'results-relocation.json'
    assert not receipt.exists(), 'Relocation already recorded'
    require_idle(inspect())
    source = Path('/var/tmp/sentrix-atm-doubao-20261112/results')
    target = BASE / 'cloud-results'
    staging = source.with_name('results-relocation-original')
    assert source.is_dir() and not source.is_symlink() and not target.exists() and not staging.exists()
    call('systemctl', '--user', 'stop', 'sentrix-atm100-doubao-photobench.service')
    try:
        before = inventory(source)
        save('results-relocation-before.json', before)
        shutil.copytree(source, target, symlinks=True)
        assert inventory(target) == before
        source.rename(staging)
        source.symlink_to(target, target_is_directory=True)
        assert inventory(source) == before
        save(receipt.name, {'source': str(source), 'target': str(target), 'files': len(before),
                            'bytes': sum(r[0] for r in before.values()), 'sha256_verified': True})
        shutil.rmtree(staging)
    finally:
        call('systemctl', '--user', 'start', 'sentrix-atm100-doubao-photobench.service')
    print(receipt.read_text())


def start_model():
    receipt = BASE / 'model-start-intent.json'
    assert not receipt.exists(), 'Model start already recorded'
    require_idle(inspect())
    assert call('systemctl', '--user', 'show', 'sentrix-atm100-model.service', '-p', 'ActiveState', '--value') in ['inactive', 'failed']
    path = Path.home() / '.config/systemd/user/sentrix-atm100-model.service.d/90-atm1013-q4km.conf'
    assert not path.exists()
    save('model-unit-before.json', {'unit': call('systemctl', '--user', 'cat', 'sentrix-atm100-model.service')})
    save(receipt.name, {'script': str(BASE / 'start_q4km.sh'), 'started_at': time.time()})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'[Service]\nExecStart=\nExecStart=/bin/bash {BASE}/start_q4km.sh\n')
    call('systemctl', '--user', 'daemon-reload')
    call('systemctl', '--user', 'start', 'sentrix-atm100-model.service')
    print(call('systemctl', '--user', 'show', 'sentrix-atm100-model.service', '-p', 'MainPID', '-p', 'ActiveState'))


def verify_model():
    assert not (BASE / 'model-acceptance.json').exists()
    require_idle(inspect())
    with urllib.request.urlopen('http://127.0.0.1:8100/props', timeout=30) as response:
        props = json.load(response)
    with urllib.request.urlopen('http://127.0.0.1:8100/v1/models', timeout=30) as response:
        models = json.load(response)
    assert props['model_alias'] == 'qwen3.5-4b-q4_k_m'
    assert props['model_ftype'] == 'Q4_K - Medium'
    assert props['default_generation_settings']['n_ctx'] == 16384 and props['total_slots'] == 4
    request = urllib.request.Request('http://127.0.0.1:8100/v1/chat/completions',
        data=json.dumps({'model': props['model_alias'], 'messages': [{'role': 'user', 'content': 'Reply exactly Q4_OK'}],
                         'max_tokens': 16, 'temperature': 0}).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        smoke = json.load(response)
    assert smoke['choices'][0]['message']['content'].strip()
    weights = {}
    for name in ['qwen35-q4km.gguf', 'qwen35-mmproj-f16.gguf']:
        path = Path('/home/realmagic/models/llama/qwen3.5') / name
        with path.open('rb') as handle:
            weights[name] = hashlib.file_digest(handle, 'sha256').hexdigest()
    save('model-api-evidence.json', {'props': props, 'models': models, 'smoke': smoke})
    call('systemctl', '--user', 'restart', 'sentrix-atm100-api.service')
    deadline = time.monotonic() + 90
    while True:
        try:
            diagnostics = api(8291, 'diagnostics')
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(2)
    assert diagnostics['runtime_configuration']['vlm_base_url'].rstrip('/') == 'http://127.0.0.1:8100/v1'
    save('product-q4-diagnostics.json', diagnostics)
    value = {'model': props['model_alias'], 'quantization': props['model_ftype'], 'slots': 4,
             'context_per_slot': 16384, 'weight_sha256': weights, 'smoke': smoke['choices'][0]['message'],
             'product_pid': call('systemctl', '--user', 'show', 'sentrix-atm100-api.service', '-p', 'MainPID', '--value')}
    save('model-acceptance.json', value)
    print(json.dumps(value))


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'inspect':
        snapshot = inspect()
        save('idle-preflight.json', snapshot)
        print(json.dumps(snapshot))
    elif action == 'move-results':
        move_results()
    elif action == 'start-model':
        start_model()
    elif action == 'verify-model':
        verify_model()
    else:
        raise ValueError(action)
