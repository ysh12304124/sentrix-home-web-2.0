"""Start one authorized full run once; save intent before POST and bind its returned ID."""
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

from prepare_runtime import BASE, api, inspect, save

ALBUM = '2026.11.17-atm-sources-addresses'
PACKAGE = Path('/var/tmp/sentrix-atm-sources-20261117') / ALBUM
QA_SHA = '13d07b33d8e9c522347bf485539a9a71d8ce569385d1ecf26620218b7e3383c3'
STAGES = {'q4km': (8872, 8291, 8100, 'qwen3.5-4b-q4_k_m'),
          'qwen37': (8874, 8294, 18108, 'qwen3.7-plus'),
          'doubao': (8874, 8294, 18107, 'doubao-seed-2.1-lite')}


def prepare():
    raw = (PACKAGE / 'qa/atm-bench.jsonl').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == QA_SHA
    assert len(raw.splitlines()) == 1013
    manifest = json.loads((PACKAGE / 'manifest.json').read_text())
    assert len(manifest['photos']) == 3759 and len(manifest['videos']) == 533
    for name in manifest['photos'] + manifest['videos']:
        assert (PACKAGE / name).is_file(), name
    assert len(json.loads((PACKAGE / 'originals/emails.json').read_text())) == 6742
    chosen = []
    for port in [8872, 8874]:
        prompts = api(port, 'judge-prompts')['kinds']
        chosen.append({row['kind'].split(':')[1]: row['custom'] or row['default']
                       for row in prompts if row['kind'].startswith('atm_qs_v1:')})
        rows = api(port, f'qa-dataset?album_id={ALBUM}&qa_set=atm-bench')['items']
        assert len(rows) == 1013
        config = api(port, 'config')
        assert config['default_judge_provider_id'] == 'ark-doubao-seed-2.1-lite'
    assert chosen[0] == chosen[1] and set(chosen[0]) == {'choice', 'number', 'open'}
    frozen = {'scoring_profiles': ['atm_qs_v1'], 'scoring_configs': {'atm_qs_v1': {
        'prompts': chosen[0], 'use_gt_images': False, 'use_gt_metadata': False,
        'gt_metadata_mode': 'image_metadata_v1', 'atm_input_version': 'native_v2'}},
        'evaluation_options': {'open_scale': 'ternary', 'choice_semantic_aux': False, 'number_semantic_aux': True},
        'judge_provider_id': 'ark-doubao-seed-2.1-lite', 'judge_model': 'doubao-seed-2.1-lite',
        'judge_url': 'https://ark.cn-beijing.volces.com/api/plan/v3', 'evidence_judge_enabled': False,
        'judge_use_gt_images': False}
    path = BASE / 'scoring-frozen.json'
    if path.exists():
        assert json.loads(path.read_text()) == frozen
    else:
        save(path.name, frozen)
    save('dataset-preflight.json', {'qa_sha256': QA_SHA, 'qa_count': 1013, 'images': 3759,
                                   'videos': 533, 'emails': 6742, 'scoring_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    print('Dataset and shared scoring configuration verified')


def launch(stage):
    port, product, endpoint, model = STAGES[stage]
    intent = BASE / f'{stage}-launch-intent.json'
    assert not intent.exists(), 'Intent already exists; inspect receipt/run list instead of repeating POST'
    prepare()
    snapshot = inspect()
    assert not snapshot['evaluators'][str(port)]['work']
    rows = snapshot['products'][str(product)]
    for table in ['agent_turns', 'import_jobs', 'video_jobs', 'pipeline_receipts']:
        assert not any(rows[table].get(s) for s in ['accepted', 'queued', 'running', 'processing', 'waiting_dependency', 'retry_scheduled']), (table, rows[table])
    assert not rows['postprocess_running']
    assert snapshot['disk']['/home']['free'] > 7 * 1024**3 and snapshot['disk']['/var/tmp']['free'] > 6 * 1024**3
    with urllib.request.urlopen(f'http://127.0.0.1:{endpoint}/v1/models', timeout=20) as response:
        models = json.load(response)
    assert models['data'][0]['id'] == model, models
    frozen = json.loads((BASE / 'scoring-frozen.json').read_text())
    payload = {**frozen, 'album_id': ALBUM, 'qa_set': 'atm-bench', 'models': ['__current__'],
               'mode': 'full', 'model_base_url': f'http://127.0.0.1:{endpoint}/v1',
               'endpoint_model': model, 'vllm_manager_url': '', 'sentrix_url': f'http://127.0.0.1:{product}',
               'reasoning': True, 'graph_enabled': True, 'qa_concurrency': 4,
               'turn_timeout': 3660, 'pipeline_timeout': 172800, 'import_timeout': 172800}
    with intent.open('x') as handle:
        json.dump({'stage': stage, 'port': port, 'payload': payload, 'at': time.time(),
                   'preflight': snapshot, 'model_discovery': models}, handle, indent=2)
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/v1/runs',
              data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.load(response)
    save(f'{stage}-launch-response.json', result)
    assert len(result['run_ids']) == 1 and result['mode'] == 'full'
    print(json.dumps({'stage': stage, 'port': port, 'model': model, **result}))


if __name__ == '__main__':
    prepare() if sys.argv[1] == 'prepare' else launch(sys.argv[1])
