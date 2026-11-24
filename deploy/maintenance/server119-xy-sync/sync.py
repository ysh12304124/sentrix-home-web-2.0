"""Synchronize only the dormant xy checkout, preserving runtime data and services."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = Path('/home/asus/xy/sentrix-home-web-2.0')
PROTECTED = ['data', 'logs', 'apps/photobench/config', 'apps/photobench/datasets',
             'apps/photobench/results', 'apps/photobench/logs', '.venv',
             'apps/web/node_modules', 'apps/photobench/web/node_modules']
SERVICES = ['sentrix-api', 'sentrix-exp-8772', 'sentrix-exp-8230', 'sentrix-https',
            'sentrix-sensevoice', 'sentrix-wemm-sidecar', 'strata-iq3xxs', 'octopus']


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def save(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def protected():
    entries = []
    for relative in PROTECTED:
        root = TARGET / relative
        if not root.exists() and not root.is_symlink():
            continue
        paths = [root]
        if root.is_dir() and not root.is_symlink():
            for directory, dirs, files in os.walk(root, followlinks=False):
                paths.extend(Path(directory) / name for name in dirs + files)
        for path in paths:
            info = path.lstat()
            entries.append((str(path.relative_to(TARGET)), info.st_dev, info.st_ino,
                            info.st_mode, info.st_size, info.st_mtime_ns,
                            os.readlink(path) if path.is_symlink() else ''))
    return {'entries': len(entries), 'identity_sha256': hashlib.sha256(
        json.dumps(sorted(entries), ensure_ascii=False).encode()).hexdigest()}


def services():
    return {name: subprocess.check_output(['systemctl', '--user', 'show', name + '.service',
                                           '-p', 'MainPID', '-p', 'NRestarts', '-p', 'ActiveState'],
                                          text=True).strip() for name in SERVICES}


def safe_path(relative):
    path = TARGET / relative
    assert not Path(relative).is_absolute() and '..' not in Path(relative).parts
    assert not any(relative == p or relative.startswith(p + '/') for p in PROTECTED)
    assert not any(parent.is_symlink() for parent in [path, *path.parents] if parent != TARGET.parent)
    return path


def plan():
    assert not (HERE / 'plan.json').exists(), 'Plan already captured'
    manifest = json.loads((HERE / 'manifest.json').read_text())
    existing, changed, obsolete, deviations = {}, [], [], []
    for relative in sorted(set(manifest['files']) | set(manifest['base_files'])):
        path = safe_path(relative)
        digest = sha(path) if path.is_file() else None
        existing[relative] = digest
        if relative in manifest['base_files'] and digest and digest != manifest['base_files'][relative]:
            deviations.append(relative)
        if relative in manifest['files']:
            if digest != manifest['files'][relative]['sha256']:
                changed.append(relative)
        elif digest is not None:
            obsolete.append(relative)
    result = dict(revision=manifest['revision'], existing=existing, changed=changed,
                  obsolete=obsolete, historical_deviations=deviations,
                  protected=protected(), services=services())
    save('plan.json', result)
    print(json.dumps({'revision': result['revision'], 'changed': len(changed),
                      'obsolete': len(obsolete), 'historical_deviations': deviations,
                      'protected': result['protected']}, ensure_ascii=False))


def apply():
    assert not (HERE / 'apply-started.json').exists(), 'Apply consumed; inspect moves and current files'
    manifest = json.loads((HERE / 'manifest.json').read_text())
    before = json.loads((HERE / 'plan.json').read_text())
    assert protected() == before['protected'] and services() == before['services']
    for relative, digest in before['existing'].items():
        path = safe_path(relative)
        assert (sha(path) if path.is_file() else None) == digest, relative
    for relative, info in manifest['files'].items():
        assert sha(HERE / 'source' / relative) == info['sha256'], relative
    save('apply-started.json', {'revision': manifest['revision']})
    backup = HERE / 'backup'
    with (HERE / 'moves.jsonl').open('w') as log:
        for relative in before['changed'] + before['obsolete']:
            target = safe_path(relative)
            if target.exists():
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
                assert sha(saved) == before['existing'][relative], relative
            if relative in before['obsolete']:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + '.sync-new')
                shutil.copyfile(HERE / 'source' / relative, temporary)
                temporary.chmod(manifest['files'][relative]['mode'])
                os.replace(temporary, target)
            log.write(json.dumps({'path': relative, 'old_sha256': before['existing'][relative],
                                  'action': 'archive-obsolete' if relative in before['obsolete'] else 'update'}) + '\n')
            log.flush()
    save('verification.json', verify())
    print((HERE / 'verification.json').read_text())


def verify():
    manifest = json.loads((HERE / 'manifest.json').read_text())
    before = json.loads((HERE / 'plan.json').read_text())
    failures = [relative for relative, info in manifest['files'].items()
                if not (TARGET / relative).is_file() or sha(TARGET / relative) != info['sha256']]
    result = dict(revision=manifest['revision'], verified_files=len(manifest['files']),
                  failures=failures, protected_unchanged=protected() == before['protected'],
                  services_unchanged=services() == before['services'], services=services())
    assert not failures and result['protected_unchanged'] and result['services_unchanged'], result
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['plan', 'apply', 'verify'])
    args = parser.parse_args()
    if args.mode == 'verify':
        print(json.dumps(verify(), ensure_ascii=False))
    else:
        {'plan': plan, 'apply': apply}[args.mode]()
