"""Finish the two-evaluator release after EnvironmentFile precedence was verified."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
RELEASE = BASE / 'full-lazy-18146f7'
HERE = RELEASE / 'web-activation-fix'
EXPECTED = {
    'q4km': '20261119-144519-2026.11.17-atm-sources-addresses-current-resume-4da9bb',
    'qwen37': '20261119-145659-2026.11.17-atm-sources-addresses-current-resume-a32d84',
}
STAGES = {
    'q4km': (8872, 8291, 'sentrix-atm100-photobench.service', Path('/home/realmagic/xy/atm-bench-20261110/product-data')),
    'qwen37': (8874, 8294, 'sentrix-atm100-doubao-photobench.service', Path('/var/tmp/sentrix-atm-doubao-20261112/product-data')),
}
PROTECTED = ['sentrix-atm100-api.service', 'sentrix-atm100-doubao-api.service', 'sentrix-atm100-model.service', 'sentrix-atm100-wemm.service', 'sentrix2-api.service', 'sentrix2-photobench.service']

def save(name, value):
    HERE.mkdir(exist_ok=True)
    with (HERE / name).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)

def api(stage, path, payload=None, product=False):
    port = STAGES[stage][1 if product else 0]
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/v1/{path}', data=None if payload is None else json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)

def pid(unit):
    return int(subprocess.check_output(['systemctl', '--user', 'show', unit, '-p', 'MainPID', '--value'], text=True))

def source(stage):
    mapping = json.loads((BASE / 'active-runs.json').read_text())[stage]
    assert mapping['run_id'] == EXPECTED[stage], 'active mapping changed'
    overview = api(stage, f"runs/{mapping['run_id']}?include_history=false")
    assert overview['storage_format'] == 3
    return {'run_id':mapping['run_id'], 'scope_id':mapping['scope_id'], 'item_count':overview['item_count'], 'status':overview['status'], 'read_revision':overview['read_revision']}

def idle(stage):
    row = source(stage)
    assert row['status'] in {'cancelled','interrupted','failed','completed','completed_with_errors'}
    assert not api(stage, 'active-work')['work'], 'evaluation cleanup remains active'
    with sqlite3.connect(f"file:{STAGES[stage][3]}/sentrix.db?mode=ro", uri=True) as db:
        assert not db.execute("select id from agent_turns where scope_id=? and status in ('pending','running')", (row['scope_id'],)).fetchall(), 'product turns remain active'
    progress = api(stage, 'imports/progress?scope_id=' + row['scope_id'], product=True)
    assert not progress['library'].get('processing') and progress['postprocess']['state']=='idle', 'product pipeline remains active'
    return row

def stop():
    before = {stage:source(stage) for stage in STAGES}
    save('stop-intent.json', before)
    for stage,row in before.items():
        if row['status'] in {'pending','running','cancelling'}:
            save(stage+'-cancel-response.json', api(stage, 'runs/'+row['run_id']+'/cancel', {}))
    deadline = time.monotonic()+300
    pending=set(STAGES)
    while pending and time.monotonic()<deadline:
        for stage in list(pending):
            try:
                row=source(stage)
                if row['status'] not in {'pending','running','cancelling'}:
                    work=api(stage,'active-work')['work']
                    if work and all(not w.get('worker_alive') and w.get('status') not in {'running','pending','cancelling'} for w in work):
                        api(stage,'active-work/cleanup',{})
                idle(stage)
                pending.remove(stage)
            except (AssertionError, OSError):
                pass
        if pending: time.sleep(2)
    assert not pending, f'not idle: {pending}'
    save('stop-response.json', {stage:idle(stage) for stage in STAGES})

def activate():
    assert (HERE/'stop-response.json').is_file()
    before={stage:idle(stage) for stage in STAGES}
    protected={unit:pid(unit) for unit in PROTECTED}
    save('activate-intent.json', {'sources':before,'protected_pids':protected})
    revision=json.loads((RELEASE/'manifest.json').read_text())['revision']
    version=HERE/'version.env'
    with version.open('x') as f:
        f.write(f'PHOTOBENCH_BUILD_REVISION={revision}+100-full-lazy\nPHOTOBENCH_SPLIT_RESULTS=1\nPHOTOBENCH_WEB_DIST_ROOT={RELEASE}/web-dist\n')
    for stage,(_,_,unit,_) in STAGES.items():
        drop=Path.home()/'.config/systemd/user'/f'{unit}.d/zzzzzzzzz-full-lazy-envfile.conf'
        with drop.open('x') as f: f.write(f'[Service]\nEnvironmentFile={version}\n')
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    for stage,(port,_,unit,_) in STAGES.items():
        subprocess.run(['systemctl','--user','restart',unit],check=True)
        deadline=time.monotonic()+90
        while True:
            try: idle(stage); break
            except (OSError,AssertionError):
                if time.monotonic()>deadline: raise
                time.sleep(1)
        environ=Path(f'/proc/{pid(unit)}/environ').read_bytes().split(b'\0')
        assert f'PHOTOBENCH_WEB_DIST_ROOT={RELEASE}/web-dist'.encode() in environ
        assert f'PHOTOBENCH_BUILD_REVISION={revision}+100-full-lazy'.encode() in environ
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/') as response: actual=response.read()
        assert actual==(RELEASE/'web-dist/index.html').read_bytes()
        assert idle(stage)['item_count']==before[stage]['item_count']
    assert {unit:pid(unit) for unit in PROTECTED}==protected
    save('activate-response.json',{'sources':before,'protected_pids':protected,'pids':{s:pid(v[2]) for s,v in STAGES.items()},'revision':revision,'index_sha256':hashlib.sha256(actual).hexdigest()})

def resume(stage,wait_id):
    import uuid
    uuid.UUID(wait_id)
    assert (HERE/'activate-response.json').is_file()
    row=idle(stage)
    plan=api(stage, f"runs/{row['run_id']}/recovery-plan")
    assert plan['can_resume'] and plan['inherited_qa_count']+plan['remaining_qa_count']==1013
    assert plan['scope_id']==row['scope_id'] and plan['frozen_scoring_profiles']==['atm_qs_v1'] and not plan['judge_use_gt_images']
    payload={'repair_missing_judges':True}
    save(stage+'-resume-intent.json',{'source':row,'plan':plan,'wait_id':wait_id,'payload':payload})
    response=api(stage,f"runs/{row['run_id']}/resume",payload)
    save(stage+'-resume-response.json',response)
    assert response['resumed_items']==plan['inherited_qa_count'] and response['scope_id']==row['scope_id']
    assert response.get('trace_quarantined_items')==(18 if stage=='q4km' else 0)
    mapping=json.loads((BASE/'active-runs.json').read_text())
    assert mapping[stage]['run_id']==EXPECTED[stage]
    mapping[stage]=response
    temp=BASE/f'.active-runs-webfix-{stage}.tmp'
    temp.write_text(json.dumps(mapping,indent=2));temp.replace(BASE/'active-runs.json')
    print(json.dumps(response),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('action',choices=['stop','activate','resume'])
    parser.add_argument('--stage',choices=STAGES)
    parser.add_argument('--wait-id')
    args=parser.parse_args()
    if args.action=='resume': resume(args.stage,args.wait_id)
    else: globals()[args.action]()
