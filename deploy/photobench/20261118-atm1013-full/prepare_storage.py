"""Losslessly compress only the six inactive deployment DB backups; retain hashes."""
import gzip
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

base = Path(sys.argv[1])
receipt = base / 'storage-preparation.json'
assert not receipt.exists(), 'Storage preparation already recorded'
rows = []
for parent in [Path('/home/realmagic/xy/deploy/20261117-psh-release/backup'), Path('/home/realmagic/xy/deploy/20261117-atm-sources-release/database-backup')]:
    for port in ['8191', '8291', '8294']:
        source = parent / port / 'sentrix.db'
        target = source.with_suffix('.db.gz')
        assert source.is_file() and not target.exists(), str(source)
        stat = source.stat()
        digest = hashlib.file_digest(source.open('rb'), 'sha256').hexdigest()
        temporary = target.with_suffix('.gz.partial')
        with source.open('rb') as src, gzip.open(temporary, 'wb', compresslevel=2) as dst:
            shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
        with gzip.open(temporary, 'rb') as decoded:
            assert hashlib.file_digest(decoded, 'sha256').hexdigest() == digest
        assert source.stat().st_size == stat.st_size and source.stat().st_mtime_ns == stat.st_mtime_ns
        temporary.chmod(stat.st_mode & 0o777)
        temporary.replace(target)
        row = {'original': str(source), 'archive': str(target), 'sha256_uncompressed': digest,
               'original_bytes': stat.st_size, 'compressed_bytes': target.stat().st_size}
        source.with_suffix('.db.archive.json').write_text(json.dumps(row, indent=2))
        source.unlink()
        rows.append(row)
        receipt.write_text(json.dumps({'archives': rows, 'completed': False}, indent=2))
        print(json.dumps(row), flush=True)
receipt.write_text(json.dumps({'archives': rows, 'completed': True,
    'bytes_saved': sum(r['original_bytes'] - r['compressed_bytes'] for r in rows),
    'home_free': shutil.disk_usage('/home').free, 'root_free': shutil.disk_usage('/var/tmp').free}, indent=2))
