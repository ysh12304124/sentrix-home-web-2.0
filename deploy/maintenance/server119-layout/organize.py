#!/usr/bin/env python3
"""Organize server119's xy workspace with a recorded, same-filesystem rename plan."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import time
import urllib.request

ROOT = Path('/home/asus/xy')
PATH_RE = re.compile(r'/home/asus/xy/[A-Za-z0-9_.+/-]+')
STATE = ROOT / 'audit/20261124-server119-layout'
EXPERIMENTS = {
    'ability-baseline-20261028', 'analysis-dimension-audit', 'analysis-usability-db8167c',
    'error-analysis-validation-f2ddba2', 'photobench-choice-eval-fix-20261107',
    'photobench-oom-fix-20261107', 'photobench-oom-read-fix-20261107',
    'refusal-metrics-20261027', 'sentrix-corpus-freeze-20261106',
    'sentrix-mobile-latency-20261106', 'sentrix-mobile-product-20261106',
    'telemetry-ui-aligned-20261029-04203d2', 'telemetry-ui-compact-20261029-09a94e9',
    'telemetry-ui-deploy-20261029-6c8c848', 'telemetry-ui-deploy-20261029-fd6d660',
    'telemetry-ui-overall-20261029-2e6e039', 'trace-deploy-20261029',
}


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=30, check=True).stdout


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def identity(path):
    st = path.lstat()
    value = {'device': st.st_dev, 'inode': st.st_ino, 'mode': st.st_mode}
    if stat.S_ISREG(st.st_mode):
        value.update(size=st.st_size, mtime_ns=st.st_mtime_ns)
        with path.open('rb') as stream:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
            value['sha256'] = digest.hexdigest()
    elif stat.S_ISDIR(st.st_mode):
        digest = hashlib.sha256()
        count = 0
        total_bytes = 0
        for folder, dirs, files in os.walk(path, followlinks=False):
            for name in sorted(dirs + files):
                child = Path(folder) / name
                item = child.lstat()
                record = [str(child.relative_to(path)), item.st_dev, item.st_ino,
                          item.st_mode, item.st_size, item.st_mtime_ns]
                if child.is_symlink():
                    target = os.readlink(child)
                    assert os.path.isabs(target), 'Inspect relative symlink before move: ' + str(child)
                    record.append(target)
                elif child.is_file():
                    total_bytes += item.st_size
                digest.update(json.dumps(record).encode())
                count += 1
            dirs.sort()
        value.update(tree_entries=count, tree_bytes=total_bytes,
                     tree_identity_sha256=digest.hexdigest())
    return value


def dependencies():
    """Record only xy paths, never environment values or complete command lines."""
    refs = {}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        paths = set()
        for filename in ['cmdline', 'environ', 'maps']:
            try:
                text = (proc / filename).read_bytes().decode(errors='replace')
                paths.update(PATH_RE.findall(text))
            except (PermissionError, FileNotFoundError, ProcessLookupError):
                pass
        try:
            for link in [proc / 'cwd', proc / 'exe', *list((proc / 'fd').iterdir())]:
                try:
                    paths.update(PATH_RE.findall(os.readlink(link)))
                except (OSError, PermissionError):
                    pass
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            pass
        if paths:
            refs['pid:' + proc.name] = sorted(paths)
    configs = []
    for base in [Path.home() / '.config/systemd/user', Path('/etc/systemd/system'),
                 Path('/etc/cron.d')]:
        if base.exists():
            configs.extend(p for p in base.rglob('*') if p.is_file())
    configs.append(Path('/etc/crontab'))
    for base in [Path.home() / 'Github/sentrix-home-web2.0',
                 Path.home() / 'Github/sentrix-home-web2.0-codex']:
        configs.extend(base.glob('.env*'))
        configs.extend((base / 'apps/photobench/config').glob('*.json'))
    for p in configs:
        try:
            if p.stat().st_size > 2_000_000:
                continue
            paths = sorted(set(PATH_RE.findall(p.read_text(errors='replace'))))
            if paths:
                refs[str(p)] = paths
        except (OSError, PermissionError):
            pass
    cron = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=10)
    cron_paths = sorted(set(PATH_RE.findall(cron.stdout)))
    if cron_paths:
        refs['user-crontab'] = cron_paths
    for base in [ROOT / 'runtime', ROOT / 'releases', ROOT / 'incoming']:
        for folder, dirs, files in os.walk(base, followlinks=False):
            for name in dirs + files:
                p = Path(folder) / name
                if p.is_symlink():
                    target = str(p.resolve())
                    if target.startswith(str(ROOT) + '/'):
                        refs['symlink:' + str(p)] = [target]
    return refs


def service_snapshot():
    names = run('systemctl', '--user', 'list-units', '--type=service', '--state=running',
                '--no-legend', '--plain').splitlines()
    units = [line.split()[0] for line in names
             if line.split()[0].startswith(('sentrix', 'strata', 'octopus'))]
    return {unit: run('systemctl', '--user', 'show', unit, '-p', 'MainPID',
                     '-p', 'ActiveState', '-p', 'NRestarts').strip() for unit in units}


def http_snapshot():
    result = {}
    for port in [8191, 8772, 8210, 8220, 8443, 19090]:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=8) as response:
                body = response.read()
                result[str(port)] = {'status': response.status,
                                     'sha256': hashlib.sha256(body).hexdigest()}
        except Exception as exc:
            result[str(port)] = {'error': type(exc).__name__}
    return result


def make_plan():
    refs = dependencies()
    referenced = {p.split('/home/asus/xy/', 1)[1].split('/', 1)[0]
                  for paths in refs.values() for p in paths}
    moves = []
    kept = []
    for p in sorted(ROOT.iterdir()):
        name = p.name
        dest = None
        alias = False
        if name in {'next-dataset.json', 'README.md'} or name.startswith('.'):
            kept.append(name)
            continue
        if p.is_file() and not p.is_symlink():
            if name.endswith(('.tar.gz', '.tgz', '.zip', '.tar')):
                group = 'packages'
            elif name.endswith(('.py', '.sh')):
                group = 'scripts'
            elif name.endswith(('.log', '.out', '.pid')):
                group = 'logs'
            else:
                group = 'evidence'
            dest = ROOT / 'archive' / group / name
        elif name in EXPERIMENTS:
            dest = ROOT / 'archive/experiments' / name
        elif name == 'sentrix-home-web-2.0':
            dest = ROOT / 'releases/legacy-sentrix-home-web-2.0'
        elif name == 'deploy-tools':
            dest = ROOT / 'scripts/deploy-tools'
        elif name in {'telemetry-probes', 'qa-taxonomy'}:
            dest = ROOT / 'audit/legacy' / name
        elif name == 'photobench_benchmark':
            dest, alias = ROOT / 'datasets/photobench/benchmark', True
        elif name == 'data-packages':
            dest, alias = ROOT / 'datasets/photobench/packages', True
        elif name == 'photobench_split80_20261023_v1':
            dest, alias = ROOT / 'datasets/photobench/split80-v1', True
        if dest is not None and (name not in referenced or alias):
            assert not dest.exists() and not dest.is_symlink(), dest
            assert p.lstat().st_dev == ROOT.stat().st_dev
            moves.append({'source': str(p), 'destination': str(dest),
                          'alias': alias, 'identity': identity(p)})
        else:
            kept.append(name)
    return {'host': socket.gethostname(), 'root': str(ROOT), 'created_at': time.time(),
            'dependencies': refs, 'moves': moves, 'kept': kept,
            'services_before': service_snapshot(), 'http_before': http_snapshot(),
            'next_link_before': os.readlink(ROOT / 'next-data-package')}


def verify(plan):
    failures = []
    for row in plan['moves']:
        src, dst = Path(row['source']), Path(row['destination'])
        if not dst.exists() or identity(dst) != row['identity']:
            failures.append({'identity_mismatch': str(dst)})
        if row['alias']:
            if not src.is_symlink() or src.resolve() != dst.resolve():
                failures.append({'alias_mismatch': str(src)})
        elif src.exists() or src.is_symlink():
            failures.append({'old_path_remains': str(src)})
    services_after, http_after = service_snapshot(), http_snapshot()
    if services_after != plan['services_before']:
        failures.append({'services_changed': True})
    if http_after != plan['http_before']:
        failures.append({'http_changed': True})
    next_link = ROOT / 'next-data-package'
    if next_link.resolve() != Path(plan['next_link_before']).resolve():
        failures.append({'next_data_package_changed': True})
    result = {'moved_count': len(plan['moves']), 'failures': failures,
              'services_after': services_after, 'http_after': http_after,
              'top_level': sorted(p.name + (' -> ' + os.readlink(p) if p.is_symlink() else '')
                                  for p in ROOT.iterdir())}
    write_json(STATE / 'verification.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert not failures, failures


def apply(plan):
    assert not (STATE / 'apply-started.json').exists(), 'Apply already started; inspect manifest.'
    current = dependencies()
    for row in plan['moves']:
        src, dst = Path(row['source']), Path(row['destination'])
        assert identity(src) == row['identity'], src
        assert not dst.exists() and not dst.is_symlink(), dst
        if not row['alias']:
            assert not any(p == str(src) or p.startswith(str(src) + '/')
                           for paths in current.values() for p in paths), src
    assert service_snapshot() == plan['services_before']
    write_json(STATE / 'apply-started.json', {'time': time.time()})
    with (STATE / 'moves.jsonl').open('x') as journal:
        for row in plan['moves']:
            src, dst = Path(row['source']), Path(row['destination'])
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            if row['alias']:
                src.symlink_to(os.path.relpath(dst, src.parent), target_is_directory=True)
            journal.write(json.dumps(row) + '\n')
            journal.flush()
            os.fsync(journal.fileno())
    link = ROOT / 'next-data-package'
    target = Path(plan['next_link_before'])
    for row in plan['moves']:
        if target.is_relative_to(row['source']):
            target = Path(row['destination']) / target.relative_to(row['source'])
    link.unlink()
    link.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)
    (ROOT / 'README.md').write_text(directory_guide(plan))
    (ROOT / 'datasets/README.md').write_text(
        '# 数据集\n\n- `ATM-Bench/`：ATM-Bench原有仓库和数据，路径保持。\n'
        '- `photobench/benchmark/`：PhotoBench相册、视频、题库和相关审计。\n'
        '- `photobench/packages/`：PhotoBench各版本数据包。\n'
        '- `photobench/split80-v1/`：旧80题子集。\n'
        '- `logs/`：原有数据集日志。\n\n运行结果与产品数据库仍按各服务原配置管理。\n')
    (ROOT / 'archive/README.md').write_text(
        '# 历史归档\n\n`packages/`为旧发布压缩包，`scripts/`为一次性历史脚本，'
        '`logs/`为旧日志，`evidence/`为散落结果文件，`experiments/`为历史验证目录。\n\n'
        '本次只移动文件，内容、权限保持；旧脚本保留其当时的绝对路径，仅用于追溯，'
        '需要复用时先按根README与迁移清单核对路径和动作，不能直接重放旧部署或测评提交。\n')
    verify(plan)


def directory_guide(plan):
    rows = '\n'.join(f"| `{Path(r['source']).relative_to(ROOT)}` | "
                     f"`{Path(r['destination']).relative_to(ROOT)}` |"
                     for r in plan['moves'])
    return '''# xy 工作目录

2026-11-24整理，设备当前地址192.168.1.119，用户asus（主机密钥与原192.168.0.153记录一致）。

| 目录 | 内容 |
|---|---|
| `projects/` | 独立研究项目与既有模型资源 |
| `datasets/` | ATM-Bench与PhotoBench数据集 |
| `releases/` | 历史发布代码副本 |
| `runtime/` | 原运行数据、配置与日志，仍按原版本区分 |
| `sentrix-mobile-20261106/` | 仍被体验服务配置引用的发行目录 |
| `audit/` | 验证记录与本次迁移清单 |
| `archive/` | 历史压缩包、脚本、日志、结果与验证实验 |
| `scripts/` | 分类后的运维工具 |
| `incoming/` | 既有待处理传输包 |

数据规范路径为`datasets/ATM-Bench`与`datasets/photobench`。原`photobench_benchmark`、
`data-packages`和`photobench_split80_20261023_v1`仅作为旧绝对路径链接，指向同一份文件，
供冻结历史、旧配置继续读取，不额外复制数据。新操作使用datasets中的规范路径。
`next-data-package`直接指向规范数据包路径；`next-dataset.json`原样保留。

主要8191产品和8772测评服务的代码位于`/home/asus/Github/sentrix-home-web2.0`，
本次没有更改源码、服务配置、数据库记录、运行历史，也没有重启服务。

迁移证据位于`audit/20261124-server119-layout/`。plan.json及moves.jsonl保存旧/新路径、
文件SHA或目录inode，verification.json保存完成验证结果。同一文件系统rename保留文件内容与权限。

历史脚本与原始证据保留其记录时间的路径；当前路径以本页映射为准。

## 路径映射

| 整理前 | 整理后 |
|---|---|
''' + rows + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['plan', 'apply', 'verify'])
    args = parser.parse_args()
    assert ROOT == Path.home() / 'xy' and socket.gethostname() == 'asus-System-Product-Name'
    assert os.getuid() == 1000
    STATE.mkdir(parents=True, exist_ok=True)
    if args.action == 'plan':
        assert not (STATE / 'plan.json').exists()
        plan = make_plan()
        write_json(STATE / 'plan.json', plan)
        print(json.dumps({'moves': len(plan['moves']), 'kept': plan['kept'],
                          'dependencies': plan['dependencies']}, ensure_ascii=False, indent=2))
    else:
        plan = json.loads((STATE / 'plan.json').read_text())
        if args.action == 'apply':
            apply(plan)
        else:
            verify(plan)


if __name__ == '__main__':
    main()
