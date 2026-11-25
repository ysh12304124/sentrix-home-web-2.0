"""Recover three already terminal product turns into a separate failed-run seed.

Only product GET requests are permitted. Source run manifests and score receipts
are retained; this script never starts a conversation, Agent, Judge or pipeline.
"""
from __future__ import annotations

import hashlib
import json
import resource
import sqlite3
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from photobench.application.run_execution.service import RunExecution, RunRequest
from photobench.domain.runs.execution import index_assets_by_name
from photobench.infrastructure.results.split_store import SplitResultWriter, SplitRunReader

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
ROOT = BASE / 'cloud-results'
SOURCE = '20261119-205809-2026.11.17-atm-sources-addresses-current-resume-4c5f90'
SEED = '20261119-qwen-terminal-turn-recovery-seed-4c5f90'
HERE = Path(__file__).resolve().parent
TURNS = {
    'atm-b767821e-3d20-4880-8de0-81c6bedf1577': 'turn_20261119125825212348',
    'atm-3aff6eaa-f88c-4301-bca8-cd9643331cfb': 'turn_20261119125825210143',
    'atm-13ec73ea-7213-4877-ab16-17211ac39982': 'turn_20261119125825211045',
}


def get(path, port=8294):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/{path}', timeout=90) as response:
        return json.load(response)


def save(name, value):
    with (HERE / name).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


class ExistingTurn:
    def __init__(self, qa, conversation, turn, question, scope):
        self.qa, self.conversation, self.turn = qa, conversation, turn
        self.question, self.scope = question, scope

    def start_conversation(self, *, title, scope_id):
        assert title == self.qa and scope_id == self.scope
        return {'id': self.conversation}

    def start_turn(self, *, conversation_id, message, reasoning, include_debug, **kwargs):
        assert conversation_id == self.conversation and message == self.question
        assert reasoning and include_debug and kwargs.get('graph_enabled', True)
        return {'turn_id': self.turn}

    def get_turn(self, *, turn_id, **kwargs):
        assert turn_id == self.turn
        body = get('assistant/turns/' + turn_id)
        assert body['status'] in {'complete', 'partial'} and body.get('result', {}).get('answer')
        return body


def main():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    assert not (HERE / 'restore-intent.json').exists(), 'single-use intent already exists'
    assert not (ROOT / SEED).exists()
    assert not get('active-work', 8874)['work']
    mapping = json.loads((BASE / 'active-runs.json').read_text())
    assert mapping['qwen37']['run_id'] == SOURCE
    source_path = ROOT / SOURCE / 'split-commit.json'
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    reader = SplitRunReader(ROOT, ROOT / SOURCE)
    state = reader.state()
    assert state['status'] == 'failed' and len(state['items']) == 1007
    assert state['qa_count'] == 1013 and state['reasoning'] and state['graph_enabled']
    assert state['runtime_snapshot']['endpoint_model'] == 'qwen3.7-plus'
    scope = state['scope_id']
    rows = {r['qa_id']: r for r in state['qa_snapshot']['rows']}
    existing = {i['qa_id'] for i in state['items']}
    assert not existing.intersection(TURNS)
    receipts_before = reader.reader.fingerprint(state['scoring_results'])
    progress = get('imports/progress?scope_id=' + scope)
    assert progress['postprocess']['state'] == 'idle' and not progress['library']['processing']
    facts = {}
    path = '/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db'
    with sqlite3.connect('file:' + path + '?mode=ro', uri=True) as db:
        assert not db.execute("SELECT id FROM agent_turns WHERE scope_id=? AND status IN ('pending','running')", (scope,)).fetchall()
        for qa, turn in TURNS.items():
            row = db.execute('SELECT t.conversation_id,c.title,t.scope_id,t.question,t.status,t.created_at,t.updated_at,t.answer FROM agent_turns t JOIN agent_conversations c ON c.id=t.conversation_id WHERE t.id=?', (turn,)).fetchone()
            assert row and row[1] == qa and row[2] == scope and row[3] == rows[qa]['question']
            assert row[4] in {'complete', 'partial'} and row[7]
            facts[qa] = {'turn_id': turn, 'conversation_id': row[0], 'status': row[4],
                         'created_at': row[5], 'updated_at': row[6], 'answer_sha256': hashlib.sha256(row[7].encode()).hexdigest()}
    save('restore-intent.json', {'source_run': SOURCE, 'source_manifest_sha256': source_sha,
                                'seed_run': SEED, 'scope_id': scope, 'terminal_turns': facts,
                                'scoring_receipts_sha256': receipts_before, 'product_http_methods': ['GET']})
    assets = []
    while True:
        page = get(f'assets?scope_id={scope}&limit=500&offset={len(assets)}&sort=created&order=asc')
        assets.extend(page['items'])
        if len(assets) >= page['total']:
            break
        assert page['items'], 'asset pagination stopped early'
    assets_by_name = index_assets_by_name(assets)
    assert len(assets) == state['qa_assets'], (len(assets), state['qa_assets'])
    restored = []
    for qa, fact in facts.items():
        client = ExistingTurn(qa, fact['conversation_id'], fact['turn_id'], rows[qa]['question'], scope)
        execution = RunExecution(client=client, albums=None, writer=None, lifecycle=None, inference=None, telemetry=None)
        request = RunRequest(album_id=state['album_id'], qa_set=state['qa_set'], model_profile=state['model_profile'],
                             sentrix_url='http://127.0.0.1:8294', reasoning=True, graph_enabled=True, turn_timeout=3660)
        item = execution._evaluate_one(rows[qa], scope, assets_by_name, request)
        assert item['execution_status'] == 'completed' and not item.get('error'), item.get('error')
        assert hashlib.sha256(item['answer'].encode()).hexdigest() == fact['answer_sha256']
        item['wall_clock_ms'] = round((datetime.fromisoformat(fact['updated_at']) - datetime.fromisoformat(fact['created_at'])).total_seconds() * 1000, 1)
        item['judge_status'] = 'pending'
        item['product_turn_recovery'] = {**fact, 'source_run_id': SOURCE, 'agent_rerun': False,
                                         'latency_source': 'product_turn_created_to_updated'}
        state['items'].append(item)
        restored.append({'qa_id': qa, **fact, 'execution_status': item['execution_status']})
    order = {r['qa_id']: n for n, r in enumerate(state['qa_snapshot']['rows'])}
    state['items'].sort(key=lambda i: order[i['qa_id']])
    state.update(run_id=SEED, status='failed', current_phase=None, resumed_from=SOURCE,
                 fatal_error='Recovery seed retains source persistence failure; three existing terminal product turns restored without inference.')
    state['terminal_turn_recovery'] = {'source_run_id': SOURCE, 'source_manifest_sha256': source_sha, 'turns': restored}
    began = time.monotonic()
    result = SplitResultWriter(ROOT).write(ROOT / SEED, state)
    verified = SplitRunReader(ROOT, ROOT / SEED)
    assert len(verified.state()['items']) == 1010
    assert verified.reader.fingerprint(verified.state()['scoring_results']) == receipts_before
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_sha
    save('restore-response.json', {'seed_run_id': SEED, 'source_manifest_unchanged': True, 'preserved_scores_sha256': receipts_before,
                                  'restored': restored, 'items': 1010, 'remaining': 3, 'write': result,
                                  'seconds': time.monotonic() - began, 'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})
    print(json.dumps({'seed_run_id': SEED, 'items': 1010, 'remaining': 3, 'write_seconds': result['seconds']}), flush=True)


if __name__ == '__main__':
    main()
