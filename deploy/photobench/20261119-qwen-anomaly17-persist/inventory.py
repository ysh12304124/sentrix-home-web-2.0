"""Read the exact failed attempt and product turns without expanding trace bodies."""
from __future__ import annotations

import hashlib
import json
import resource
import shutil
import sqlite3
import urllib.request
from datetime import datetime
from pathlib import Path

from photobench.domain.evaluation.profiles import valid_result
from photobench.infrastructure.results.split_json import STORED_TYPES
from photobench.infrastructure.results.split_store import SplitRunReader

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
ROOT = BASE / 'cloud-results'
SOURCE = '20261119-213836-2026.11.17-atm-sources-addresses-current-resume-0e3686'
FAILED = '20261119-221456-2026.11.17-atm-sources-addresses-current-resume-170bf7'
PLAN = BASE / 'qwen-live-list-8b6da36/anomaly17-plan.json'


def api(port, path):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/{path}', timeout=30) as response:
        return json.load(response)


def identity(value):
    if isinstance(value, STORED_TYPES):
        return {'immutable_object_ref': value.ref}
    if isinstance(value, dict):
        return {k: identity(v) for k, v in value.items()}
    if isinstance(value, list):
        return [identity(v) for v in value]
    return value


def summary(item):
    return {**{k: item.get(k) for k in ('qa_id', 'execution_status', 'agent_status',
        'termination_reason', 'judge_status', 'error', 'wall_clock_ms')},
        'answer_length': len(item.get('answer') or ''),
        'answer_sha256': hashlib.sha256((item.get('answer') or '').encode()).hexdigest(),
        'turns': [{k: turn.get(k) for k in ('turn_id', 'conversation_id', 'status')}
                  for turn in item.get('runtime_turns') or []]}


def main():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    plan = json.loads(PLAN.read_text())
    targets = set(plan['qa_ids'])
    mapping = json.loads((BASE / 'active-runs.json').read_text())
    assert mapping['qwen37']['run_id'] == FAILED
    source = SplitRunReader(ROOT, ROOT / SOURCE)
    current = SplitRunReader(ROOT, ROOT / FAILED)
    old, new = source.state(), current.state()
    prior = {i['qa_id']: i for i in old['items']}
    actual = {i['qa_id']: i for i in new['items']}
    inherited = set(prior) - targets
    assert len(inherited) == 996 and inherited <= actual.keys()
    assert all(identity(prior[q]) == identity(actual[q]) for q in inherited)
    prior_scores = {k: v for k, v in old['scoring_results'].items() if v['qa_id'] in inherited}
    actual_scores = {k: new['scoring_results'][k] for k in prior_scores}
    assert source.reader.fingerprint(prior_scores) == current.reader.fingerprint(actual_scores)
    database = '/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db'
    with sqlite3.connect('file:' + database + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        columns = [r[1] for r in db.execute('PRAGMA table_info(agent_turns)')]
        active = [dict(r) for r in db.execute("SELECT id,scope_id,status,created_at,updated_at FROM agent_turns WHERE status IN ('pending','running')")]
        marks = ','.join('?' for _ in targets)
        turns = [dict(r) for r in db.execute(
            'SELECT c.title AS qa_id,t.id,t.conversation_id,t.status,t.created_at,t.updated_at,'
            't.termination_reason,length(t.answer) AS answer_length,'
            'length(t.trace_json) AS trace_json_bytes FROM agent_turns t '
            'JOIN agent_conversations c ON c.id=t.conversation_id '
            f'WHERE t.scope_id=? AND c.title IN ({marks}) AND julianday(t.created_at)>=julianday(?) '
            'ORDER BY t.created_at', (new['scope_id'], *targets, new['started_at']))]
    q4_dir = Path('/home/realmagic/xy/atm-bench-20261110/results') / mapping['q4km']['run_id']
    q4 = json.loads((q4_dir / 'split-commit.json').read_text())['monitor']
    result = {
        'observed_at': datetime.now().astimezone().isoformat(),
        'source_run_id': SOURCE, 'run_id': FAILED,
        'status': new['status'], 'fatal_error': new.get('fatal_error'),
        'scope_id': new['scope_id'], 'item_count': len(actual), 'qa_count': new['qa_count'],
        'inherited_items_and_scores_verified': len(inherited),
        'source_manifest_sha256': hashlib.sha256((ROOT / SOURCE / 'split-commit.json').read_bytes()).hexdigest(),
        'failed_manifest_sha256': hashlib.sha256((ROOT / FAILED / 'split-commit.json').read_bytes()).hexdigest(),
        'runtime_snapshot': identity(new['runtime_snapshot']),
        'valid_atm_scores': sum(valid_result(v) for v in new['scoring_results'].values()),
        'attempts': [{'qa_id': q, 'old': summary(prior[q]),
            'new': summary(actual[q]) if q in actual else None} for q in plan['qa_ids']],
        'missing_qa_ids': sorted(targets - actual.keys()),
        'product_turns': turns, 'active_product_turns': active,
        'agent_turn_columns': columns,
        'active_work': api(8874, 'active-work'),
        'product_imports': api(8294, 'imports/progress?scope_id=' + new['scope_id']),
        'q4': {k: q4.get(k) for k in ('run_id', 'status', 'item_count', 'qa_count')},
        'q4_active_work': api(8872, 'active-work'),
        'disk_free': {p: shutil.disk_usage(p).free for p in ('/home', '/var/tmp')},
        'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
