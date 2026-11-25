# One-run Q4 trace quarantine

This release path is pinned to the failed 8872 run `20261119-021603-2026.11.17-atm-sources-addresses-current-resume-62cc28`, its scope, and checkpoint generation `6d296ab38f414887a55fc0aade1672b0`. It removes trajectory fields only for the 18 listed QA IDs from the derived resume seed. It preserves each answer, execution state, score, failure record, scoring batch, the 1013-question denominator, and the original immutable checkpoint.

`build_seed.py` first streams the source to locate the target item indexes, then streams it again to omit the known trajectory fields before constructing the reduced state. The QA ID scan is independent of JSON object key order. A checksum- and generation-bound manifest is published only after the production resume loader validates the candidate seed. Existing artifacts cause the builder to stop; it never replaces a seed.

Marked items retain their current Agent and Judge results and are omitted from automatic recovery work. No automatic or explicit Agent retry is allowed for a marked item in this recovery. No other run or QA ID is affected.

Run only after copying the committed `build_seed.py`, `activation.json`, and `resume_quarantine.py` into the exact release tree and confirming the source run has no active worker. The activation file is specific to the exact run and generation above.
