"""Exercise real 1010-item persistence concurrently with the live-list read path."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import resource
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from photobench.application.run_execution.state import RunState
from photobench.application.runs.suites import RunSuites
from photobench.infrastructure.results.store import FileResultWriter, FileRunStore
from photobench.infrastructure.results.split_store import SplitRunReader

BASE = Path('/home/realmagic/xy/experiments/atm1013-20261118')
ROOT = BASE / 'cloud-results'
RUN = '20261119-qwen-terminal-turn-recovery-seed-4c5f90'
HERE = Path(__file__).resolve().parent


class ValidationWriter(FileResultWriter):
    def _run_dir(self, run_id):
        assert run_id == RUN
        return HERE / 'live-write-validation' / RUN


def main():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    assert not (HERE / 'live-write-response.json').exists()
    os.environ['PHOTOBENCH_SPLIT_RESULTS'] = '1'
    logging.basicConfig(filename=HERE / 'live-write.log', level=logging.WARNING)
    pointer = ROOT / RUN / 'split-commit.json'
    before = hashlib.sha256(pointer.read_bytes()).hexdigest()
    source = SplitRunReader(ROOT, ROOT / RUN)
    state = source.state()
    assert len(state['items']) == 1010
    state.update(status='running', current_phase='qa_eval')
    live = RunState(state, run_id=RUN, writer=ValidationWriter(ROOT))
    suites = object.__new__(RunSuites)
    suites._store = FileRunStore(ROOT)
    suites._lock = threading.RLock()
    suites._active = {RUN: SimpleNamespace(state=live)}
    suites._live_entries = {RUN: suites._live_list_entry(state)}
    stop = threading.Event()
    counts = []

    def read_loop():
        count = 0
        while not stop.is_set():
            entry = suites.active_states()[RUN]
            assert entry['item_count'] == 1010 and entry['status'] == 'running'
            count += 1
            stop.wait(.1)
        return count

    live.start_persist_writer()
    times = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        reads = [pool.submit(read_loop) for _ in range(2)]
        try:
            for n in range(2):
                live.record_phase('qa_eval', 'validation_progress', n)
                started = time.monotonic()
                live.persist(wait=True)
                times.append(time.monotonic() - started)
        finally:
            stop.set()
            counts = [task.result() for task in reads]
            live.stop_persist_writer()
    target = SplitRunReader(ROOT, HERE / 'live-write-validation' / RUN)
    assert len(target.state()['items']) == 1010
    assert target.reader.fingerprint(target.state()['scoring_results']) == source.reader.fingerprint(source.state()['scoring_results'])
    assert hashlib.sha256(pointer.read_bytes()).hexdigest() == before
    result = {'items': 1010, 'writes': len(times), 'write_seconds': times, 'live_list_reads': sum(counts),
              'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'source_manifest_unchanged': True, 'score_receipts_unchanged': True}
    with (HERE / 'live-write-response.json').open('x') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
