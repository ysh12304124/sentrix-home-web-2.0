"""Read-only verification of inherited answers, scores and live lazy APIs."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from photobench.infrastructure.results.split_store import SplitRunReader

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
ROOT = BASE / 'cloud-results'
HERE = Path(__file__).resolve().parent
SOURCE = '20261119-qwen-terminal-turn-recovery-seed-4c5f90'


def main():
    run = json.loads((HERE / 'resume-response.json').read_text())['run_id']
    source = SplitRunReader(ROOT, ROOT / SOURCE)
    current = SplitRunReader(ROOT, ROOT / run)
    old, new = source.state(), current.state()
    prior = {i['qa_id']: i for i in old['items']}
    by_id = {i['qa_id']: i for i in new['items']}
    assert len(prior) == 1010 and prior.keys() <= by_id.keys()
    fields = ['qa_id', 'question', 'reference_answer', 'answer', 'execution_status',
              'agent_status', 'termination_reason', 'attachments', 'predicted_media',
              'model_call_metrics', 'tool_trace', 'runtime_turns']
    # Execution traces are immutable references; compare their content digests,
    # avoiding traversal of old prompts and full response bodies.
    from photobench.infrastructure.results.split_json import STORED_TYPES

    def references(value):
        if isinstance(value, STORED_TYPES):
            return {'immutable_object_ref': value.ref}
        if isinstance(value, dict):
            return {key: references(child) for key, child in value.items()}
        if isinstance(value, list):
            return [references(child) for child in value]
        return value

    for qa, item in prior.items():
        for field in fields:
            a, b = item.get(field), by_id[qa].get(field)
            assert references(a) == references(b), (qa, field)
    old_scores = old['scoring_results']
    score_subset = {k: new['scoring_results'][k] for k in old_scores}
    score_sha = source.reader.fingerprint(old_scores)
    assert score_sha == current.reader.fingerprint(score_subset)
    paths = ['active-work', 'runs', f'runs/{run}?include_history=false',
             f'runs/{run}/items?page=1&page_size=20', f'runs/{run}/items/528',
             f'runs/{run}/items/528/trace?limit=50']
    timings = []
    for path in paths:
        began = time.monotonic()
        with urllib.request.urlopen('http://127.0.0.1:8874/api/v1/' + path, timeout=30) as response:
            raw = response.read()
            body = json.loads(raw)
        timings.append({'path': path, 'seconds': time.monotonic() - began, 'bytes': len(raw)})
        if '/trace?' in path:
            assert len(body['nodes']) <= 50
    result = {'run_id': run, 'inherited_answers_verified': len(prior), 'inherited_scoring_receipts': len(old_scores),
              'score_receipts_sha256': score_sha, 'source_revision': source.revision,
              'current_revision': current.revision, 'online_apis': timings}
    with (HERE / 'preservation-response.json').open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
