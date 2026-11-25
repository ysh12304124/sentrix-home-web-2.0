"""Publish an isolated PhotoBench recovery copy to 8872/8874, preserving 8772."""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from prepare_runtime import BASE, api, save
from resume_checkpoint import intent, original, preflight

SOURCE = Path('/home/realmagic/xy/releases/psh-b2030e1-100-20261117')
TARGET = BASE / 'resume-release'
RELATIVE = Path('src/photobench/application/runs/suites.py')
EXPECTED = '810e39e2f4bc8b66fc8605b04ea31863ed3ecd10bf29b727f6c1caa0b3af6358'
UNITS = ['sentrix-atm100-photobench.service', 'sentrix-atm100-doubao-photobench.service']
PROTECTED = ['sentrix2-api.service', 'sentrix2-photobench.service']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pid(unit):
    return subprocess.check_output(['systemctl', '--user', 'show', unit, '-p', 'MainPID', '--value'],
                                   text=True).strip()


def install(revision):
    assert not TARGET.exists()
    old_file = SOURCE / 'apps/photobench' / RELATIVE
    assert sha(old_file) == EXPECTED
    before = {stage: preflight(stage) for stage in ['q4km', 'qwen37']}
    protected = {unit: pid(unit) for unit in PROTECTED}
    intent('resume-install-intent.json', {'revision': revision, 'preflight': before,
                                        'protected_pids': protected, 'at': time.time()})
    (TARGET / 'apps').mkdir(parents=True)
    shutil.copytree(SOURCE / 'apps/photobench', TARGET / 'apps/photobench', symlinks=True)
    destination = TARGET / 'apps/photobench' / RELATIVE
    shutil.copy2(BASE / 'resume-suites.py', destination)
    subprocess.run([str(SOURCE / '.venv/bin/python'), '-m', 'py_compile', str(destination)], check=True)
    version = BASE / 'resume-version.env'
    version.write_text(f'PHOTOBENCH_BUILD_REVISION={revision}+100-atm1013-resume\n'
                       f'PHOTOBENCH_SENTRIX_REPO_ROOT={SOURCE}\n')
    for unit in UNITS:
        dropin = Path.home() / '.config/systemd/user' / (unit + '.d') / 'zz-atm1013-resume.conf'
        assert not dropin.exists()
        port = 8872 if unit == UNITS[0] else 8874
        dropin.write_text('[Service]\nEnvironmentFile=' + str(version) + '\nExecStart=\nExecStart='
            + str(SOURCE / '.venv/bin/python') + ' -m uvicorn --factory photobench.main:create_app'
            + f' --host 0.0.0.0 --port {port} --app-dir {TARGET}/apps/photobench/src\n')
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'restart', *UNITS], check=True)
    for port in [8872, 8874]:
        deadline = time.monotonic() + 120
        while True:
            try:
                assert not api(port, 'active-work')['work']
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(2)
    assert {unit: pid(unit) for unit in PROTECTED} == protected
    assert sha(old_file) == EXPECTED
    for stage in before:
        assert original(stage)[1] == before[stage]['pointer_sha256']
    result = {'revision': revision, 'target': str(TARGET), 'suites_sha256': sha(destination),
              'original_suites_sha256': sha(old_file), 'protected_pids': protected,
              'pids': {unit: pid(unit) for unit in UNITS}, 'original_checkpoints_unchanged': True}
    save('resume-install-response.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    install(sys.argv[1])
