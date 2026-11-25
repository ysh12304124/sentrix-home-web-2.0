"""Restore the existing Q4 GPU process after startup CUDA initialization failed."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

HERE=Path(__file__).resolve().parent
BASE=Path('/home/realmagic/xy/experiments/atm1013-20261118')
EXPECTED='20261119-154238-2026.11.17-atm-sources-addresses-current-resume-b5c51a'
MODEL='sentrix-atm100-model.service'
GPU='GPU-bd82fa9d-0906-ce2c-9932-a2c021551203'
PROTECTED=['sentrix-atm100-api.service','sentrix-atm100-photobench.service','sentrix-atm100-doubao-api.service','sentrix-atm100-doubao-photobench.service','sentrix-atm100-wemm.service','sentrix2-api.service','sentrix2-photobench.service']
DATA=Path('/home/realmagic/xy/atm-bench-20261110/product-data')

def save(name,value):
 with (HERE/name).open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2)

def pid(unit):return int(subprocess.check_output(['systemctl','--user','show',unit,'-p','MainPID','--value'],text=True))

def api(path,payload=None,port=8872):
 request=urllib.request.Request(f'http://127.0.0.1:{port}/api/v1/{path}',data=None if payload is None else json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(request,timeout=30) as r:return json.load(r)

def source():
 mapping=json.loads((BASE/'active-runs.json').read_text())['q4km'];assert mapping['run_id']==EXPECTED
 r=api('runs/'+EXPECTED+'?include_history=false');return {k:r[k] for k in ['run_id','scope_id','status','item_count','runtime_snapshot','storage_format']}

def idle():
 r=source();assert r['status'] in {'cancelled','failed','interrupted','completed_with_errors'}
 assert not api('active-work')['work']
 with sqlite3.connect(f'file:{DATA}/sentrix.db?mode=ro',uri=True) as db:
  rows=db.execute("select id,status,cancel_requested from agent_turns where status in ('pending','running')").fetchall()
  assert all(s=='pending' and c and i.startswith('turn_20261110') for i,s,c in rows),rows
 progress=api('imports/progress',port=8291);assert not progress['library']['processing'] and progress['postprocess']['state']=='idle'
 return r

def device_processes():
 lines=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,gpu_uuid,used_memory','--format=csv,noheader,nounits'],text=True).splitlines()
 return [{'pid':int(a.strip()),'gpu_uuid':b.strip(),'used_memory_mib':int(c.strip())} for a,b,c in (line.split(',') for line in lines if line.strip())]

def stop():
 save('stop-intent.json',{'source':source(),'model_pid':pid(MODEL),'gpu_processes':device_processes(),'protected':{u:pid(u) for u in PROTECTED}})
 save('cancel-response.json',api('runs/'+EXPECTED+'/cancel',{}))
 deadline=time.monotonic()+300
 while True:
  try:
   if source()['status'] not in {'running','pending','cancelling'}:
    rows=api('active-work')['work']
    if rows and all(not x.get('worker_alive') and x.get('status') not in {'running','pending','cancelling'} for x in rows):api('active-work/cleanup',{})
   row=idle();break
  except (AssertionError,OSError):
   if time.monotonic()>deadline:raise
   time.sleep(2)
 save('stop-response.json',row)

def restore():
 row=idle();assert (HERE/'stop-response.json').is_file()
 protected={u:pid(u) for u in PROTECTED};oldpid=pid(MODEL)
 assert not any(x['pid']==oldpid for x in device_processes()),'model already acquired GPU'
 script=BASE/'start_q4km.sh';script_hash=hashlib.sha256(script.read_bytes()).hexdigest()
 free=int(subprocess.check_output(['nvidia-smi',f'--id={GPU}','--query-gpu=memory.free','--format=csv,noheader,nounits'],text=True).strip());assert free>6000,free
 save('restore-intent.json',{'source':row,'protected':protected,'old_model_pid':oldpid,'gpu_free_mib':free,'start_script_sha256':script_hash})
 subprocess.run(['systemctl','--user','restart',MODEL],check=True)
 deadline=time.monotonic()+120
 while True:
  try:
   with urllib.request.urlopen('http://127.0.0.1:8100/health',timeout=3) as r:assert json.load(r)['status']=='ok'
   rows=device_processes();current=pid(MODEL)
   gpu=next(x for x in rows if x['pid']==current and x['gpu_uuid']==GPU and x['used_memory_mib']>2000)
   break
  except (OSError,AssertionError,StopIteration):
   if time.monotonic()>deadline:raise
   time.sleep(1)
 assert oldpid!=current and protected=={u:pid(u) for u in PROTECTED}
 assert hashlib.sha256(script.read_bytes()).hexdigest()==script_hash
 with urllib.request.urlopen('http://127.0.0.1:8100/v1/models') as r:models=json.load(r)
 assert models['data'][0]['id']=='qwen3.5-4b-q4_k_m'
 with urllib.request.urlopen('http://127.0.0.1:8100/slots') as r:slots=json.load(r)
 assert len(slots)==4 and all(not x['is_processing'] and x['n_ctx']==16384 for x in slots)
 save('restore-response.json',{'model_pid':current,'gpu':gpu,'models':models,'slots':[{k:x[k] for k in ['id','n_ctx','is_processing']} for x in slots],'protected':protected,'script_sha256':script_hash})

def resume(wait_id):
 uuid.UUID(wait_id);assert (HERE/'restore-response.json').is_file();row=idle();plan=api('runs/'+EXPECTED+'/recovery-plan')
 assert plan['can_resume'] and plan['inherited_qa_count']+plan['remaining_qa_count']==1013 and plan['frozen_scoring_profiles']==['atm_qs_v1']
 save('resume-intent.json',{'source':row,'plan':plan,'wait_id':wait_id,'payload':{'repair_missing_judges':True}})
 r=api('runs/'+EXPECTED+'/resume',{'repair_missing_judges':True});save('resume-response.json',r)
 assert r['resumed_items']==plan['inherited_qa_count'] and r['scope_id']==row['scope_id'] and r['trace_quarantined_items']==18
 mapping=json.loads((BASE/'active-runs.json').read_text());assert mapping['q4km']['run_id']==EXPECTED;mapping['q4km']=r
 temp=HERE/'active-runs.new.json';temp.write_text(json.dumps(mapping,indent=2));os.replace(temp,BASE/'active-runs.json');print(json.dumps(r),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser(__doc__);p.add_argument('action',choices=['stop','restore','resume']);p.add_argument('--wait-id');a=p.parse_args()
 if a.action=='resume':resume(a.wait_id)
 else:globals()[a.action]()
