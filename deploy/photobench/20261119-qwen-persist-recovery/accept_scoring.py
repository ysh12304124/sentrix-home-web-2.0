"""Accept six Judge results and verify every pre-existing valid score receipt."""
import json
from pathlib import Path
import urllib.request

from photobench.domain.evaluation.profiles import valid_result
from photobench.infrastructure.results.split_store import SplitRunReader

HERE = Path(__file__).resolve().parent
BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
ROOT = BASE / 'cloud-results'


def main():
    plan = json.loads((HERE / 'scoring-plan.json').read_text())
    response = json.loads((HERE / 'scoring-response.json').read_text())
    run_id = plan['run_id']
    assert json.loads((BASE / 'active-runs.json').read_text())['qwen37']['run_id'] == run_id
    with urllib.request.urlopen('http://127.0.0.1:8874/api/v1/active-work', timeout=30) as stream:
        assert not json.load(stream)['work']
    reader = SplitRunReader(ROOT, ROOT / run_id)
    state = reader.state()
    activity = state['rejudge']
    assert activity['rejudge_id'] == response['rejudge_id']
    assert activity['status'] == 'completed' and activity['completed'] == activity['total'] == 6
    assert activity['failed'] == 0 and not activity.get('error')
    valid = {k: v for k, v in state['scoring_results'].items() if valid_result(v)}
    assert len(valid) == 1011
    previous = {k: valid[k] for k in plan['valid_score_keys']}
    assert len(previous) == plan['valid_scores_before'] == 1005
    assert reader.reader.fingerprint(previous) == plan['valid_score_receipts_sha256']
    targets = plan['targets']
    assert len(targets) == 6 and all(valid[t['key']]['qa_id'] == t['qa_id'] for t in targets)
    failures = sorted(i['qa_id'] for i in state['items'] if i['execution_status'] != 'completed')
    assert failures == plan['retained_execution_failures']
    receipt = {'run_id': run_id, 'rejudge_id': activity['rejudge_id'],
        'items': len(state['items']), 'valid_atm_scores': len(valid),
        'unchanged_previous_valid_score_receipts': len(previous),
        'previous_receipts_sha256': plan['valid_score_receipts_sha256'],
        'retained_execution_failures': failures,
        'six_results': [{'qa_id': t['qa_id'], 'key': t['key'],
                         'score': valid[t['key']]['score'],
                         'status': valid[t['key']]['status']} for t in targets]}
    with (HERE / 'scoring-acceptance.json').open('x') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
