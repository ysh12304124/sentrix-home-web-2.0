"""Read-only verification of the 996 inherited answers and new-attempt mapping."""
import hashlib
import json
from pathlib import Path

from photobench.infrastructure.results.split_json import STORED_TYPES
from photobench.infrastructure.results.split_store import SplitRunReader

HERE = Path(__file__).resolve().parent
BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
ROOT = BASE / 'cloud-results'


def references(value):
    if isinstance(value, STORED_TYPES):
        return {'immutable_object_ref': value.ref}
    if isinstance(value, dict):
        return {k: references(v) for k, v in value.items()}
    if isinstance(value, list):
        return [references(v) for v in value]
    return value


def main():
    intent = json.loads((HERE / 'anomaly17-seed-intent.json').read_text())
    response = json.loads((HERE / 'anomaly17-resume-response.json').read_text())
    source = SplitRunReader(ROOT, ROOT / intent['source_run_id'])
    current = SplitRunReader(ROOT, ROOT / response['run_id'])
    old, new = source.state(), current.state()
    selected = set(intent['qa_ids'])
    prior = {i['qa_id']: i for i in old['items'] if i['qa_id'] not in selected}
    actual = {i['qa_id']: i for i in new['items']}
    assert len(old['items']) == old['qa_count'] == new['qa_count'] == 1013
    assert len(prior) == 996 and prior.keys() <= actual.keys()
    assert actual.keys() - prior.keys() <= selected
    fields = ['question', 'reference_answer', 'answer', 'execution_status', 'agent_status',
              'termination_reason', 'attachments', 'predicted_media', 'scope_id',
              'model_call_metrics', 'tool_trace', 'runtime_turns']
    for qa, item in prior.items():
        for field in fields:
            assert references(item.get(field)) == references(actual[qa].get(field)), (qa, field)
    scores = {k: new['scoring_results'][k] for k, v in old['scoring_results'].items()
              if v['qa_id'] not in selected}
    assert current.reader.fingerprint(scores) == intent['inherited_scores_sha256']
    assert hashlib.sha256((ROOT / intent['source_run_id'] / 'split-commit.json').read_bytes()).hexdigest() == intent['source_manifest_sha256']
    assert set(new['resume_context']['inherited_qa_ids']) == set(prior)
    result = {'run_id': response['run_id'], 'source_run_id': intent['source_run_id'],
        'inherited_answers_verified': len(prior), 'inherited_scoring_receipts_verified': len(scores),
        'source_manifest_unchanged': True, 'new_attempt_qa_ids': sorted(selected),
        'new_attempts_saved_so_far': sorted(actual.keys() - prior.keys()),
        'scope_id': new['scope_id'], 'execution_budgets': new['runtime_snapshot']['execution_budgets']}
    with (HERE / 'anomaly17-preservation.json').open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
