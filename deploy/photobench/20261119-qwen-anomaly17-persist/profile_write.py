"""Profile a separate real-result checkpoint; source manifests remain untouched."""
from __future__ import annotations

import cProfile
import hashlib
import io
import json
import pstats
import resource
import time
from pathlib import Path

from photobench.infrastructure.results.split_store import SplitResultWriter, SplitRunReader

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
ROOT = BASE / 'cloud-results'
RUN = '20261119-221456-2026.11.17-atm-sources-addresses-current-resume-170bf7'
HERE = Path(__file__).resolve().parent


def main():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    assert not (HERE / 'profile-response.json').exists()
    source = ROOT / RUN / 'split-commit.json'
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    reader = SplitRunReader(ROOT, ROOT / RUN)
    state = reader.state()
    profiles = cProfile.Profile()
    started = time.monotonic()
    profiles.enable()
    result = SplitResultWriter(ROOT).write(HERE / 'write-validation' / RUN, state)
    profiles.disable()
    stream = io.StringIO()
    pstats.Stats(profiles, stream=stream).sort_stats('cumulative').print_stats(35)
    (HERE / 'profile.txt').write_text(stream.getvalue())
    profiles.dump_stats(HERE / 'write.prof')
    after = SplitRunReader(ROOT, HERE / 'write-validation' / RUN)
    assert len(after.state()['items']) == len(state['items'])
    assert after.reader.fingerprint(after.state()['scoring_results']) == reader.reader.fingerprint(state['scoring_results'])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    receipt = {'seconds': time.monotonic() - started, 'write': result,
        'items': len(state['items']), 'source_manifest_unchanged': True,
        'scores_unchanged': True, 'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    with (HERE / 'profile-response.json').open('x') as output:
        json.dump(receipt, output, indent=2)
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
