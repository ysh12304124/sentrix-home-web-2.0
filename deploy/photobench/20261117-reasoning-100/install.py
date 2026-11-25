"""One-shot deployment for the checked local release; does not submit evaluation work."""
from pathlib import Path
import json,shutil,subprocess,time,urllib.request,sqlite3
from preflight import inspect,SERVICES,sha
BASE=Path(__file__).resolve().parent
manifest=json.loads((BASE/'manifest.json').read_text())
intent=BASE/'intent.json'
assert not intent.exists(),'This deployment has already started; inspect its receipt before continuing.'

def idle(snapshot):
    for port,item in snapshot['services'].items():
        if int(port)>=8700:
            assert item['active_work']['work']==[],f'{port}: evaluation is busy'
        else:
            for data in item['databases'].values():
                turns=data.get('active_turns',[])
                assert all(r[1]=='pending' and r[2].startswith('2026-11-10') for r in turns),turns
                assert len(turns)==(10 if port=='8291' else 0),turns
                for table in ['import_jobs','video_jobs']:
                    assert all(status not in ['running','queued','retry_scheduled','waiting_dependency','processing'] or count==0 for status,count in data.get(table+'_statuses',[])),table

def wait(port,path):
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/{path}',timeout=4) as response:
                if response.status==200:return
        except OSError:time.sleep(1)
    raise RuntimeError(f'{port}: health did not become ready')

def backup(path):
    dest=BASE/'backup'/str(path).lstrip('/')
    dest.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and not dest.exists():shutil.copy2(path,dest)

before=inspect(manifest)
idle(before)
for port,item in before['services'].items():
    variant=port if int(port)<8700 else 'photobench'
    assert item['source']=={p:v['before'] for p,v in manifest['variants'][variant]['files'].items()},f'{port}: source changed'
for path,value in manifest['payload'].items():
    assert sha(BASE/path)==value,path
(BASE/'before.json').write_text(json.dumps(before,indent=2)+'\n')
intent.write_text(json.dumps({'revision':manifest['source_revision'],'started_at':time.time()},indent=2)+'\n')
print('Preflight passed; stopping evaluation services',flush=True)
for port in [8772,8872,8874]:
    subprocess.run(['systemctl','--user','stop',SERVICES[port][0]],check=True,timeout=90)
print('Stopping paired product services',flush=True)
# Inspect persisted product work again after stopping the request producers.
for port in [8191,8291,8294]:
    prior=before['services'][str(port)]
    for db,stats in prior['databases'].items():
        with sqlite3.connect(Path(db).as_uri()+'?mode=ro',uri=True) as connection:
            turns=[list(r) for r in connection.execute("select id,status,created_at from agent_turns where status in ('pending','running') order by id")]
            assert turns==stats['active_turns'],f'{port}: product became busy'
            for table in ['import_jobs','video_jobs']:
                if table+'_statuses' in stats:
                    assert [list(r) for r in connection.execute('select status,count(*) from '+table+' group by status')]==stats[table+'_statuses'],table
    subprocess.run(['systemctl','--user','stop',SERVICES[port][0]],check=True,timeout=90)

for port in [8191,8291,8294,8772,8872]:
    root=Path(SERVICES[port][1]);variant=SERVICES[port][2]
    for relative,values in manifest['variants'][variant]['files'].items():
        target=root/relative
        assert sha(target)==values['before'],str(target)
        backup(target);shutil.copy2(BASE/'payload'/variant/relative,target)
    build=root/'build-info.json'
    backup(build)
    value=json.loads(build.read_text());value['revision']=manifest['deployed_version']
    build.write_text(json.dumps(value,indent=2)+'\n')

for port in [8772,8872]:
    dist=Path(before['services'][str(port)]['dist_root'])
    # Install new hashed assets before switching index.html; preserve old assets for open pages.
    for source in sorted((BASE/'dist').rglob('*'),key=lambda p:p.name=='index.html'):
        if not source.is_file():continue
        target=dist/source.relative_to(BASE/'dist')
        backup(target);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)

version=BASE/'version.env'
version.write_text('PHOTOBENCH_BUILD_REVISION='+manifest['deployed_version']+'\n')
for port in [8772,8872,8874]:
    cfg=Path(before['services'][str(port)]['env']['PHOTOBENCH_CONFIG_ROOT'])/'runtime_connection.json'
    backup(cfg)
    value=json.loads(cfg.read_text());value['product_build_revision']=manifest['deployed_version']
    cfg.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    drop=Path.home()/'.config/systemd/user'/(SERVICES[port][0]+'.d')/'99zz-reasoning-revision.conf'
    assert not drop.exists(),str(drop)
    drop.write_text('[Service]\nEnvironmentFile='+str(version)+'\n')

subprocess.run(['systemctl','--user','daemon-reload'],check=True)
print('Starting updated products',flush=True)
for port in [8191,8291,8294]:
    subprocess.run(['systemctl','--user','start',SERVICES[port][0]],check=True,timeout=15)
    wait(port,'health/live');print(f'{port}: ready',flush=True)
print('Starting updated evaluation services',flush=True)
for port in [8772,8872,8874]:
    subprocess.run(['systemctl','--user','start',SERVICES[port][0]],check=True,timeout=15)
    wait(port,'chat/sessions');print(f'{port}: ready',flush=True)

after=inspect(manifest)
idle(after)
for port,item in after['services'].items():
    variant=port if int(port)<8700 else 'photobench'
    assert item['source']=={p:v['after'] for p,v in manifest['variants'][variant]['files'].items()},f'{port}: deployed source mismatch'
    prior=before['services'][port]
    if int(port)<8700:
        assert item['databases']==prior['databases'],f'{port}: database inventory changed'
    else:
        assert item['result_inventory']==prior['result_inventory'],f'{port}: historical result changed'
        assert item['configured_product_revision']==manifest['deployed_version']
        assert item['env']['PHOTOBENCH_BUILD_REVISION']==manifest['deployed_version']
        for relative,value in manifest['dist'].items():assert item['dist_files'][relative]==value
for path,value in before['protected'].items():
    if Path(path).name=='runtime_connection.json':
        previous=json.loads((BASE/'backup'/path.lstrip('/')).read_text())
        current=json.loads(Path(path).read_text())
        assert current=={**previous,'product_build_revision':manifest['deployed_version']},path
    else:assert after['protected'][path]==value,path
(BASE/'after.json').write_text(json.dumps(after,indent=2)+'\n')
(BASE/'acceptance.json').write_text(json.dumps({'status':'completed','revision':manifest['source_revision'],'deployed_version':manifest['deployed_version'],'pids':{p:v['pid'] for p,v in after['services'].items()},'protected_files':len(before['protected']),'historical_results':{p:len(v['result_inventory']) for p,v in after['services'].items() if int(p)>=8700}},indent=2)+'\n')
print((BASE/'acceptance.json').read_text(),flush=True)
