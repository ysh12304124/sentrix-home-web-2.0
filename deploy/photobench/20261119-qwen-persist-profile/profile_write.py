"""Profile first publication of the recovered answer in an isolated object store."""
from __future__ import annotations

import cProfile
import argparse
import hashlib
import json
import pstats
import resource
import time
from pathlib import Path

from photobench.domain.runs.frozen_trace import freeze_item_traces
from photobench.infrastructure.results.split_json import thaw
from photobench.infrastructure.results.split_store import SplitResultWriter, SplitRunReader

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
SOURCE = BASE / 'cloud-results'
RUN = '20261119-205809-2026.11.17-atm-sources-addresses-current-resume-4c5f90'
QA = 'atm-1e644e18-6645-43fa-bb0c-156d88692427'
HERE = Path(__file__).resolve().parent


def main(full=False):
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    pointer = SOURCE / RUN / 'split-commit.json'
    before = hashlib.sha256(pointer.read_bytes()).hexdigest()
    reader = SplitRunReader(SOURCE, SOURCE / RUN)
    state = reader.state()
    index = next(i for i, item in enumerate(state['items']) if item['qa_id'] == QA)
    started = time.monotonic()
    item = thaw(reader.item_record(index))
    print(json.dumps({'loaded_seconds': time.monotonic() - started,
                      'rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}), flush=True)
    if full:
        state['items'][index] = item
    else:
        state['items'] = [item]
        state['scoring_results'] = {k: v for k, v in state.get('scoring_results', {}).items() if v['qa_id'] == QA}
        state['diagnostic_results'] = {k: v for k, v in state.get('diagnostic_results', {}).items() if v['qa_id'] == QA}
        state['qa_snapshot'] = {**state['qa_snapshot'], 'rows': [r for r in state['qa_snapshot']['rows'] if r['qa_id'] == QA]}
    freeze_item_traces(state['items'])
    output = HERE / ('full' if full else 'isolated')
    assert not output.exists(), 'profile output already exists'
    profiler = cProfile.Profile()
    writer = SplitResultWriter(SOURCE if full else output)
    profiler.enable()
    result = writer.write(output / RUN, state)
    profiler.disable()
    profiler.dump_stats(str(output / 'write.pstats'))
    with (output / 'profile.txt').open('x') as stream:
        pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(70)
    assert before == hashlib.sha256(pointer.read_bytes()).hexdigest()
    result.update(source_pointer_sha256=before, qa_id=QA,
                  peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    (output / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--full', action='store_true')
    main(parser.parse_args().full)
