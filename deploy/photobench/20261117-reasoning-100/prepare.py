from pathlib import Path
import subprocess, tempfile, tarfile, io, shutil, json, hashlib
repo=Path('/Users/jzousz/.codex/worktrees/telemetry-latest-main/sentrix-local1171-resume-20261031')
audit=Path('/Users/jzousz/Documents/ChatGPT/产品项目/audit/20261117-reasoning-100-release')
stage=Path(tempfile.mkdtemp(prefix='sentrix-reasoning100-'))
def git(*args): return subprocess.check_output(['git',*args],cwd=repo)
def extract(ref,target):
 target.mkdir()
 with tarfile.open(fileobj=io.BytesIO(git('archive',ref))) as archive: archive.extractall(target,filter='data')
extract('b2030e1',stage/'base');extract('HEAD',stage/'current')
production=[p for p in git('diff','--name-only','b2030e1','HEAD','--','apps/api/src','apps/photobench/src').decode().splitlines() if p.endswith('.py')]
manifest={'source_revision':git('rev-parse','HEAD').decode().strip(),'stage':str(stage),'variants':{}}
for name,patches in [('8191',['8191.patch']),('8291',['8291.patch']),('8294',[]),('photobench',['8772.patch'])]:
 old=stage/('old-'+name);new=stage/('new-'+name)
 shutil.copytree(stage/'base',old);shutil.copytree(stage/'current',new)
 for patch in patches:
  p=repo/'deploy/photobench/20261117-psh-100'/patch
  subprocess.run(['git','apply',str(p)],cwd=old,check=True,capture_output=True)
  result=subprocess.run(['git','apply',str(p)],cwd=new,capture_output=True,text=True)
  if result.returncode: print(name,result.stderr);raise SystemExit(1)
 if name=='photobench': subprocess.run(['git','apply',str(repo/'deploy/photobench/20261117-chat-revision/hotfix.patch')],cwd=old,check=True,capture_output=True)
 paths=[p for p in production if p.startswith('apps/photobench/src' if name=='photobench' else 'apps/api/src')]
 manifest['variants'][name]={'files':{p:{'before':hashlib.sha256((old/p).read_bytes()).hexdigest(),'after':hashlib.sha256((new/p).read_bytes()).hexdigest()} for p in paths},'old':str(old),'new':str(new)}
(audit/'stage.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'stage':str(stage),'variants':{k:len(v['files']) for k,v in manifest['variants'].items()}}))
