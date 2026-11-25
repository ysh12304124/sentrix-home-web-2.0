"""Restore the existing GPU model and resume each original checkpoint once."""
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from inspect_reboot import GPU1, STAGES as ORIGINAL, gpu_snapshot, scope_snapshot
from launch import STAGES
from prepare_runtime import BASE, api, save


def original(stage):
    run_id, root, _ = ORIGINAL[stage]
    directory = root / run_id
    pointer = directory / 'snapshot-commit.json'
    generation = json.loads(pointer.read_text())['generation']
    state = json.loads((directory / '.snapshots' / generation / 'run.json').read_text())
    return state, hashlib.sha256(pointer.read_bytes()).hexdigest()


def preflight(stage):
    port, product, _, _ = STAGES[stage]
    assert not api(port, 'active-work')['work']
    assert api(product, 'health')['status'] == 'ok'
    run, pointer_sha = original(stage)
    assert run['status'] == 'interrupted' and len(run['pipeline_asset_ids']) == 11034
    assert not run.get('items') and len(run['qa_snapshot']['rows']) == 1013
    assert run['reasoning'] is True and run['graph_enabled'] is True
    budgets = run['runtime_snapshot']['execution_budgets']
    assert budgets['turn_timeout'] == 3660 and budgets['qa_concurrency'] == 4
    plan = api(port, f'runs/{run["run_id"]}/recovery-plan')
    assert plan['can_resume'] and plan['remaining_qa_count'] == 1013
    assert not plan['product_version_changed'] and not plan['judge_use_gt_images']
    with sqlite3.connect(f'file:{ORIGINAL[stage][2]}?mode=ro', uri=True) as db:
        turns = dict(db.execute('select status,count(*) from agent_turns group by status'))
        assert not any(count for status, count in turns.items() if status in {'running', 'processing'})
        receipts = list(db.execute("select scope_id,started_at from pipeline_receipts where status='running'"))
        for scope, started in receipts:
            assert scope == run['scope_id'] and started < '2026-11-17T23:00:00', receipts
    return {'run_id': run['run_id'], 'scope_id': run['scope_id'], 'pointer_sha256': pointer_sha,
            'budgets': budgets, 'recovery_plan': plan, 'scope': scope_snapshot(stage), 'turns': turns}


def intent(name, value):
    with (BASE / name).open('x') as handle:
        json.dump(value, handle, indent=2)


def restore_model():
    before = {stage: preflight(stage) for stage in ORIGINAL}
    assert gpu_snapshot()['gpu_ready']
    rows = subprocess.check_output(['nvidia-smi', '--query-gpu=uuid,memory.free',
                                    '--format=csv,noheader,nounits'], text=True)
    assert any(line.split(',')[0].strip() == GPU1 and int(line.split(',')[1]) > 14000
               for line in rows.splitlines())
    intent('gpu-model-restore-intent.json', {'preflight': before, 'at': time.time()})
    subprocess.run(['systemctl', '--user', 'restart', 'sentrix-atm100-model.service'], check=True)
    deadline = time.monotonic() + 150
    while True:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8100/props', timeout=5) as response:
                props = json.load(response)
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(2)
    assert props['model_alias'] == 'qwen3.5-4b-q4_k_m' and props['model_ftype'] == 'Q4_K - Medium'
    assert props['total_slots'] == 4 and props['default_generation_settings']['n_ctx'] == 16384
    pid = subprocess.check_output(['systemctl', '--user', 'show', 'sentrix-atm100-model.service',
                                   '-p', 'MainPID', '--value'], text=True).strip()
    processes = subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid,gpu_uuid,used_memory',
                                         '--format=csv,noheader,nounits'], text=True)
    assert any(row.split(',')[0].strip() == pid and row.split(',')[1].strip() == GPU1
               for row in processes.splitlines()), processes
    request = urllib.request.Request('http://127.0.0.1:8100/v1/chat/completions',
        data=json.dumps({'model': props['model_alias'], 'messages': [{'role': 'user', 'content':
            'Reply exactly Q4_OK'}], 'max_tokens': 16, 'temperature': 0}).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        smoke = json.load(response)
    assert smoke['choices'][0]['message']['content'].strip() == 'Q4_OK'
    save('gpu-model-restore-response.json', {'pid': int(pid), 'gpu': GPU1, 'gpu_processes': processes,
         'model': props['model_alias'], 'slots': 4, 'context_per_slot': 16384, 'smoke': smoke})
    print(json.dumps({'pid': int(pid), 'gpu': GPU1, 'smoke': 'Q4_OK'}))


def resume(stage):
    assert (BASE / 'resume-install-response.json').exists()
    assert (BASE / 'gpu-model-restore-response.json').exists()
    before = preflight(stage)
    port, _, endpoint, model = STAGES[stage]
    with urllib.request.urlopen(f'http://127.0.0.1:{endpoint}/v1/models', timeout=15) as response:
        assert json.load(response)['data'][0]['id'] == model
    payload = {'finish_incomplete_build': True}
    intent(f'{stage}-resume-intent.json', {'before': before, 'payload': payload, 'at': time.time()})
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/v1/runs/{before["run_id"]}/resume',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.load(response)
    save(f'{stage}-resume-response.json', result)
    assert result['resumed_from'] == before['run_id'] and result['scope_id'] == before['scope_id']
    assert result['resumed_items'] == 0 and result['run_id'] != before['run_id']
    assert original(stage)[1] == before['pointer_sha256']
    path = BASE / 'active-runs.json'
    mapping = json.loads(path.read_text()) if path.exists() else {}
    assert stage not in mapping
    mapping[stage] = result
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(mapping, indent=2) + '\n')
    temporary.replace(path)
    print(json.dumps({'stage': stage, **result}))


if __name__ == '__main__':
    if sys.argv[1] == 'preflight':
        print(json.dumps({stage: preflight(stage) for stage in ORIGINAL}))
    elif sys.argv[1] == 'restore-model':
        restore_model()
    elif sys.argv[1] == 'resume':
        resume(sys.argv[2])
    else:
        raise ValueError('Unknown action')
