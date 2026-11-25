"""One-use Q4 read-memory repair; original checkpoints and the parallel run stay intact."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
OLD = BASE / 'reasoning-bounds-ffec2be/release/evaluation'
RELEASE = HERE / 'evaluation'
PYTHON = '/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python'
UNIT = 'sentrix-atm100-photobench.service'
RESULTS = Path('/home/realmagic/xy/atm-bench-20261110/results')
SUITES = 'apps/photobench/src/photobench/application/runs/suites.py'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    with (HERE / name).open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def api(path, payload=None, timeout=30):
    request = urllib.request.Request('http://127.0.0.1:8872/api/v1/' + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return json.load(r)


def pid(unit):
    return subprocess.check_output(['systemctl','--user','show',unit,'-p','MainPID','--value'],text=True).strip()


def metadata(run_id):
    d=RESULTS/run_id
    pointer=d/'snapshot-commit.json'
    generation=json.loads(pointer.read_text())['generation']
    overview=json.loads((d/'.snapshots'/generation/'metadata.json').read_text())['overview']
    return {'run_id':run_id,'pointer_sha':sha(pointer),'scope_id':overview['scope_id'],
            'status':overview['status'],'item_count':overview['item_count']}


def idle():
    assert not any(w.get('worker_alive') for w in api('active-work')['work'])
    source=json.loads((BASE/'active-runs.json').read_text())['q4km']
    with sqlite3.connect('file:/home/realmagic/xy/atm-bench-20261110/product-data/sentrix.db?mode=ro',uri=True) as db:
        turns=list(db.execute("select id,status from agent_turns where scope_id=? and status in ('pending','running')",(source['scope_id'],)))
        assert not turns,turns
    before=metadata(source['run_id'])
    assert before['status']=='interrupted',before
    return before


def stage():
    manifest=json.loads((HERE/'manifest.json').read_text())
    assert not RELEASE.exists()
    for relative,info in manifest['files'].items():
        assert sha(OLD/relative)==info['before'],relative
        assert sha(HERE/'payload'/relative)==info['after'],relative
    save('stage-intent.json',{'source':idle(),'parallel_pid':pid('sentrix-atm100-doubao-photobench.service'),
                             'baseline':str(OLD),'revision':manifest['revision']})
    shutil.copytree(OLD,RELEASE,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    for relative in manifest['files']:
        target=RELEASE/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(HERE/'payload'/relative,target)
    # This deployment has a documented cloud/startup overlay. Apply only the committed method.
    target=RELEASE/SUITES
    content=target.read_text()
    marker='    def active_states(self) -> dict[str, dict[str, Any]]:'
    method=(HERE/'active_snapshot.txt').read_text()
    assert content.count(marker)==1 and 'def active_snapshot(' not in content
    target.write_text(content.replace(marker,method+marker,1))
    assert sha(target)==manifest['suites_after']
    tests=['apps/photobench/tests/test_run_read.py','apps/photobench/tests/test_trace_checkpoint_reuse.py',
           'apps/photobench/tests/test_suites.py']
    for relative in tests:
        target=RELEASE/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(HERE/'payload'/relative,target)
    env={**os.environ,'PYTHONPATH':str(RELEASE/'apps/photobench/src')}
    with (HERE/'candidate-tests.txt').open('x') as log:
        subprocess.run([PYTHON,'-m','pytest','-o','addopts=','-q',*tests],cwd=RELEASE,env=env,
                       stdout=log,stderr=subprocess.STDOUT,check=True)
    save('stage-response.json',{'sha_verified':True,'tests':(HERE/'candidate-tests.txt').read_text()[-1000:]})


def activate():
    before=idle()
    manifest=json.loads((HERE/'manifest.json').read_text())
    assert (HERE/'stage-response.json').is_file()
    protected={u:pid(u) for u in ['sentrix-atm100-doubao-photobench.service','sentrix-atm100-api.service',
        'sentrix-atm100-doubao-api.service','sentrix-atm100-model.service','sentrix2-api.service','sentrix2-photobench.service']}
    save('activate-intent.json',{'source':before,'protected_pids':protected})
    drop=Path.home()/'.config/systemd/user'/f'{UNIT}.d/zzzzzz-oom-read-reuse.conf'
    assert not drop.exists()
    drop.write_text('[Service]\nExecStart=\nExecStart='+PYTHON+' -m uvicorn --factory photobench.main:create_app'
        ' --host 0.0.0.0 --port 8872 --app-dir '+str(RELEASE/'apps/photobench/src')+'\n'
        'Environment=PHOTOBENCH_BUILD_REVISION='+manifest['revision']+'+100-active-trace-reuse\n')
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','restart',UNIT],check=True)
    for attempt in range(60):
        try:
            work=api('active-work',timeout=3)
            break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError('restarted evaluator did not become ready')
    assert not any(w.get('worker_alive') for w in work['work'])
    assert metadata(before['run_id'])==before
    assert protected=={u:pid(u) for u in protected}
    save('activate-response.json',{'pid':pid(UNIT),'source_unchanged':True,'protected_pids':protected})


def resume(wait_id):
    assert wait_id and len(wait_id)==36
    source=idle()
    # A plan parses the legacy checkpoint. The local caller waits for the saved receipt.
    plan=api(f"runs/{source['run_id']}/recovery-plan",timeout=900)
    save('recovery-plan.json',plan)
    assert plan['can_resume'] and plan['scope_id']==source['scope_id']
    assert plan['inherited_qa_count']+plan['remaining_qa_count']==1013
    assert plan['inherited_qa_count']==95 and plan['frozen_scoring_profiles']==['atm_qs_v1']
    assert not plan['judge_use_gt_images']
    payload={'repair_missing_judges':True}
    save('resume-intent.json',{'source':source,'plan':plan,'wait_id':wait_id,'payload':payload})
    response=api(f"runs/{source['run_id']}/resume",payload,timeout=900)
    save('resume-response.json',response)
    assert response['resumed_items']==95 and response['resumed_from']==source['run_id']
    assert response['scope_id']==source['scope_id'] and not response['retried_qa_ids']
    assert metadata(source['run_id'])==source
    path=BASE/'active-runs.json';mapping=json.loads(path.read_text());mapping['q4km']=response
    temporary=path.with_suffix('.oom-recovery.tmp');temporary.write_text(json.dumps(mapping,indent=2)+'\n')
    os.replace(temporary,path)
    print(json.dumps(response))


if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('action',choices=['stage','activate','resume'])
    parser.add_argument('--wait-id')
    args=parser.parse_args()
    if args.action=='resume':resume(args.wait_id)
    elif args.action=='stage':stage()
    else:activate()
