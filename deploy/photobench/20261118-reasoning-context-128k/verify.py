"""Read-only acceptance after service startup, also usable after a dependency is restored."""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, '/home/realmagic/xy/deploy/20261117-atm-sources-release')
from preflight import SERVICES, inspect

base = Path(sys.argv[1])
manifest = json.loads((base / 'manifest.json').read_text())
before = json.loads((base / 'before.json').read_text())
after = inspect(manifest)
for port, item in after['services'].items():
    prior = before['services'][port]
    variant = SERVICES[int(port)][2]
    assert item['source'] == {p: row['after'] for p, row in manifest['variants'][variant]['files'].items()}
    if int(port) < 8700:
        assert item['databases'] == prior['databases'], (port, 'database inventory changed')
        assert item['build_info']['revision'] == manifest['deployed_version']
        endpoint = 'health/live'
    else:
        assert item['active_work']['work'] == [], (port, 'busy')
        for key in ['result_inventory', 'dist_files', 'env']:
            assert item[key] == prior[key], (port, key)
        assert item['configured_product_revision'] == manifest['deployed_version']
        endpoint = 'active-work'
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/{endpoint}', timeout=10) as response:
        assert response.status == 200
for path, value in before['protected'].items():
    if Path(path).name == 'runtime_connection.json':
        previous = json.loads((base / 'backup' / path.lstrip('/')).read_text())
        current = json.loads(Path(path).read_text())
        assert current == {**previous, 'product_build_revision': manifest['deployed_version']}
    else:
        assert after['protected'][path] == value, path
runtime = {}
for port in [8191, 8291, 8294]:
    pid = after['services'][str(port)]['pid']
    env = dict(x.decode().split('=', 1) for x in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in x)
    configured = env.get('SENTRIX_VLM_REASONING_CONTEXT_TOKENS')
    assert configured == ('131072' if port in [8191, 8294] else None)
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/diagnostics', timeout=20) as response:
        details = json.load(response)
    settings = details['runtime_configuration']
    ordinary_context = int(env.get('SENTRIX_VLM_CONTEXT_TOKENS', '16384' if port == 8191 else '6144'))
    assert settings['vlm_context_tokens'] == ordinary_context
    assert settings.get('vlm_reasoning_context_tokens') == (131072 if configured else None)
    runtime[str(port)] = {'ordinary_context_tokens': ordinary_context, 'declared_reasoning_tokens': settings.get('vlm_reasoning_context_tokens'), 'embeddings': details.get('embeddings')}
receipt = {'status': 'passed', 'revision': manifest['source_revision'], 'deployed_version': manifest['deployed_version'],
    'pids': {port: item['pid'] for port, item in after['services'].items()}, 'runtime': runtime,
    'historical_results_and_chat_preserved': True, 'database_inventory_preserved': True}
(base / 'after.json').write_text(json.dumps(after, indent=2) + '\n')
(base / 'acceptance.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt))
