"""Publish the verified Q4 sampling configuration once, preserving the active scope."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
OLD_PRODUCT = BASE / 'reasoning-bounds-ffec2be/release/q4km'
OLD_EVALUATION = BASE / 'full-lazy-18146f7/evaluation'
PRODUCT = HERE / 'product'
EVALUATION = HERE / 'evaluation'
PYTHON = '/home/realmagic/xy/releases/psh-b2030e1-100-20261117/.venv/bin/python'
EXPECTED = '20261119-151026-2026.11.17-atm-sources-addresses-current-resume-86771b'
PRODUCT_UNIT = 'sentrix-atm100-api.service'
EVALUATION_UNIT = 'sentrix-atm100-photobench.service'
PROTECTED = ['sentrix-atm100-doubao-api.service', 'sentrix-atm100-doubao-photobench.service', 'sentrix-atm100-model.service', 'sentrix-atm100-wemm.service', 'sentrix2-api.service', 'sentrix2-photobench.service']
DATA = Path('/home/realmagic/xy/atm-bench-20261110/product-data')
SUITES = Path('apps/photobench/src/photobench/application/runs/suites.py')
BASELINE_SHA = 'fd20af69ec9c9029a65c423b8384ce0a00c627e4218de3959dc8e2d64f369288'
LIMITS = {'reasoning_max_model_calls':100, 'reasoning_max_tool_calls':600, 'reasoning_max_search_calls':100, 'reasoning_max_other_tool_calls':500}


def save(name, value):
    with (HERE / name).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def api(path, payload=None, product=False):
    port = 8291 if product else 8872
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/v1/{path}', data=None if payload is None else json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def pid(unit):
    return int(subprocess.check_output(['systemctl','--user','show',unit,'-p','MainPID','--value'],text=True))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source():
    mapping=json.loads((BASE/'active-runs.json').read_text())['q4km']
    assert mapping['run_id']==EXPECTED, 'active mapping changed'
    row=api('runs/'+EXPECTED+'?include_history=false')
    assert row['storage_format']==3 and row['qa_count']==1013 and row['reasoning'] and row['graph_enabled']
    assert row['execution_budgets']['qa_concurrency']==4 and row['execution_budgets']['turn_timeout']==3660
    return {key:row[key] for key in ['run_id','scope_id','status','item_count','runtime_snapshot','read_revision']}


def idle():
    row=source()
    assert row['status'] in {'cancelled','failed','interrupted','completed_with_errors'}
    assert not api('active-work')['work'], 'evaluation worker or cleanup remains active'
    with sqlite3.connect(f'file:{DATA}/sentrix.db?mode=ro',uri=True) as db:
        rows=db.execute("select id,status,cancel_requested from agent_turns where status in ('pending','running')").fetchall()
        assert all(s=='pending' and c and i.startswith('turn_20261110') for i,s,c in rows), rows
    progress=api('imports/progress',product=True)
    assert not progress['library']['processing'] and progress['postprocess']['state']=='idle'
    return row


def stage():
    intent={'source':source(),'protected_pids':{u:pid(u) for u in PROTECTED},'product_unit_pid':pid(PRODUCT_UNIT),'evaluation_unit_pid':pid(EVALUATION_UNIT)}
    assert sha(OLD_EVALUATION/SUITES)==BASELINE_SHA
    revision=(HERE/'source-revision.txt').read_text().strip()
    assert len(revision)==40
    save('stage-intent.json',intent)
    shutil.copytree(OLD_PRODUCT,PRODUCT,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    shutil.copytree(OLD_EVALUATION,EVALUATION,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    path=EVALUATION/SUITES;before=path.read_text()
    old='                            "reasoning_max_other_tool_calls",\n                        )'
    new='                            "reasoning_max_other_tool_calls",\n                            "vlm_generation_profile",\n                        )'
    assert before.count(old)==1
    after=before.replace(old,new)
    def methods(text):
        return {n.name:ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(text)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    a,b=methods(before),methods(after)
    assert [k for k in a if a[k]!=b[k]]==['resume_run']
    path.write_text(after)
    product_revision='ffec2be63ed31c0e3f77268c030a77b8029cf43f+100-q4-formal-'+revision[:7]
    manifest=json.loads((PRODUCT/'build-info.json').read_text());manifest.update(revision=product_revision,configuration_revision=revision)
    (PRODUCT/'build-info.json').write_text(json.dumps(manifest,indent=2)+'\n')
    shutil.copyfile(HERE/'qwen35-q4km.env.example',HERE/'formal.env')
    (HERE/'evaluation.env').write_text(f'PHOTOBENCH_BUILD_REVISION={revision}+100-q4-formal\n')
    result={**intent,'source_revision':revision,'product_revision':product_revision,'suites_sha256':sha(path),'environment_sha256':sha(HERE/'formal.env'),'limits':LIMITS,'generation_profile':'qwen35_nonthinking'}
    save('stage-response.json',result)
    print(json.dumps(result),flush=True)


def stop():
    save('stop-intent.json',source())
    save('cancel-response.json',api('runs/'+EXPECTED+'/cancel',{}))
    deadline=time.monotonic()+300
    while True:
        try:
            row=source()
            if row['status'] not in {'running','pending','cancelling'}:
                work=api('active-work')['work']
                if work and all(not w.get('worker_alive') and w.get('status') not in {'running','pending','cancelling'} for w in work):
                    api('active-work/cleanup',{})
            row=idle();break
        except (AssertionError,OSError):
            if time.monotonic()>deadline:raise
            time.sleep(2)
    save('stop-response.json',row)
    print(json.dumps(row),flush=True)


def activate():
    assert (HERE/'stop-response.json').is_file() and (HERE/'candidate-tests.json').is_file()
    assert json.loads((HERE/'candidate-tests.json').read_text())['status']=='passed'
    row=idle();staged=json.loads((HERE/'stage-response.json').read_text())
    protected={u:pid(u) for u in PROTECTED}
    assert protected==staged['protected_pids']
    save('activate-intent.json',{'source':row,'protected_pids':protected})
    for unit,app,envfile,root in [(PRODUCT_UNIT,'sentrix',HERE/'formal.env',PRODUCT),(EVALUATION_UNIT,'photobench',HERE/'evaluation.env',EVALUATION)]:
        appdir=root/('apps/api/src' if app=='sentrix' else 'apps/photobench/src')
        drop=Path.home()/'.config/systemd/user'/f'{unit}.d/zzzzzzzzzzzz-psh-q4-formal.conf'
        with drop.open('x') as f:
            f.write(f'[Service]\nWorkingDirectory={root}\nEnvironmentFile={envfile}\nExecStart=\nExecStart={PYTHON} -m uvicorn --factory {app}.main:create_app --host 0.0.0.0 --port {8291 if app=="sentrix" else 8872} --app-dir {appdir}\n')
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    for unit in [PRODUCT_UNIT,EVALUATION_UNIT]:
        subprocess.run(['systemctl','--user','restart',unit],check=True)
        deadline=time.monotonic()+120
        while True:
            try:
                api('health/live',product=True) if unit==PRODUCT_UNIT else api('config');break
            except OSError:
                if time.monotonic()>deadline:raise
                time.sleep(1)
    finish_activation()


def finish_activation():
    assert (HERE/'activate-intent.json').is_file()
    assert not (HERE/'activate-response.json').exists()
    intent=json.loads((HERE/'activate-intent.json').read_text())
    row=idle();staged=json.loads((HERE/'stage-response.json').read_text())
    protected=intent['protected_pids']
    for unit,root in [(PRODUCT_UNIT,PRODUCT),(EVALUATION_UNIT,EVALUATION)]:
        assert Path(os.readlink(f'/proc/{pid(unit)}/cwd'))==root
    env=dict(part.decode().split('=',1) for part in Path(f'/proc/{pid(PRODUCT_UNIT)}/environ').read_bytes().split(b'\0') if b'=' in part)
    assert env['SENTRIX_VLM_GENERATION_PROFILE']=='qwen35_nonthinking'
    for k,v in LIMITS.items():assert int(env['SENTRIX_'+k.upper()])==v
    assert api('health/live',product=True)['build_revision']==staged['product_revision']
    config=api('config')['runtime_config']
    assert config['product_build_revision']==row['runtime_snapshot']['product_build_revision']
    config_path=Path(api('config')['runtime_config_file'])
    before=json.loads(config_path.read_text())
    assert before['product_build_revision']==row['runtime_snapshot']['product_build_revision']
    updated={**before,'product_build_revision':staged['product_revision'],'product_settings':{**before['product_settings'],**LIMITS,'vlm_generation_profile':'qwen35_nonthinking'}}
    save('config-file-intent.json',{'path':str(config_path),'before':before,'after':updated,'before_sha256':sha(config_path)})
    temporary=HERE/'runtime_connection.new.json'
    temporary.write_text(json.dumps(updated,ensure_ascii=False,indent=2)+'\n')
    os.replace(temporary,config_path)
    subprocess.run(['systemctl','--user','restart',EVALUATION_UNIT],check=True)
    deadline=time.monotonic()+90
    while True:
        try:
            actual=api('config')['runtime_config'];break
        except OSError:
            if time.monotonic()>deadline:raise
            time.sleep(1)
    assert actual['product_build_revision']==staged['product_revision']
    assert actual['product_settings']['vlm_generation_profile']=='qwen35_nonthinking'
    save('config-response.json',{'product_build_revision':actual['product_build_revision'],'product_settings':actual['product_settings'],'sha256':sha(config_path)})
    assert source()['item_count']==row['item_count']
    assert {u:pid(u) for u in PROTECTED}==protected
    save('activate-response.json',{'source':row,'product_revision':staged['product_revision'],'product_pid':pid(PRODUCT_UNIT),'evaluation_pid':pid(EVALUATION_UNIT),'protected_pids':protected})


def resume(wait_id):
    uuid.UUID(wait_id)
    assert (HERE/'activate-response.json').is_file()
    row=idle();plan=api('runs/'+EXPECTED+'/recovery-plan')
    assert plan['can_resume'] and plan['inherited_qa_count']+plan['remaining_qa_count']==1013
    assert plan['scope_id']==row['scope_id'] and plan['frozen_scoring_profiles']==['atm_qs_v1'] and not plan['judge_use_gt_images']
    payload={'repair_missing_judges':True,'product_build_revision':json.loads((HERE/'stage-response.json').read_text())['product_revision']}
    save('resume-intent.json',{'source':row,'plan':plan,'wait_id':wait_id,'payload':payload})
    response=api('runs/'+EXPECTED+'/resume',payload);save('resume-response.json',response)
    assert response['resumed_items']==plan['inherited_qa_count'] and response['scope_id']==row['scope_id'] and response['trace_quarantined_items']==18
    mapping=json.loads((BASE/'active-runs.json').read_text());assert mapping['q4km']['run_id']==EXPECTED
    mapping['q4km']=response;temp=HERE/'active-runs.new.json';temp.write_text(json.dumps(mapping,indent=2));os.replace(temp,BASE/'active-runs.json')
    print(json.dumps(response),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(__doc__);p.add_argument('action',choices=['stage','stop','activate','finish_activation','resume']);p.add_argument('--wait-id');a=p.parse_args()
    if a.action=='resume':resume(a.wait_id)
    else:globals()[a.action]()
