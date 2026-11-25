"""Measure the live-list projection on the failed run without modifying results."""
from __future__ import annotations

import cProfile
import json
import pstats
import resource
import time
from pathlib import Path

from photobench.domain.runs.analysis import run_list_entry
from photobench.infrastructure.results.split_store import SplitRunReader

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
RUN = '20261119-205809-2026.11.17-atm-sources-addresses-current-resume-4c5f90'
HERE = Path(__file__).resolve().parent


def main():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    state = SplitRunReader(BASE / 'cloud-results', BASE / 'cloud-results' / RUN).state()
    profiler = cProfile.Profile()
    began = time.monotonic()
    profiler.enable()
    entry = run_list_entry(state)
    profiler.disable()
    result = {'seconds': time.monotonic() - began, 'item_count': len(state['items']),
              'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'status': entry['status']}
    with (HERE / 'live-profile.txt').open('x') as stream:
        pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(50)
    (HERE / 'live-result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
