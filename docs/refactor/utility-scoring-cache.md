# Full-pool utility scoring: cache and concurrency

Local Mac startup leaves utility scoring disabled by default. Set
`SENTRIX_ENABLE_UTILITY_VLM=1` explicitly to opt in again. Graph retrieval does
not depend on the scorer and retains its ordinary graph-slot fallback.

Graph-off requests never run the utility selector. Graph-on requests still score
every image in the deduplicated primary/graph candidate pool before selecting
the head. Non-images remain explicit unscored fallbacks. The ranking value is
the first-token `True` **log probability**, not an unavailable raw logit.

The API container owns a single shared scorer. Its semaphore permits at most
two concurrent scoring HTTP requests across searches/QA workers **in that API
process**. Deploy one API worker for this local setup; independent API processes
do not share the semaphore. Scoring client threads waiting for it do not consume
extra model slots. Identical in-flight scoring requests share one result.

Successful scores are cached in a bounded memory map and in
`<SENTRIX_DATA_DIR>/utility-cache/scores.sqlite` (at most 50,000 stored scores).
Keys hash the exact query, original image bytes, served model ID, endpoint,
preprocessing/prompt version, and configured cache revision. Failures are never
cached. The persistent database contains hashes/scores, not image/question data.
The encoded-image cache holds at most 64 images and does not persist raw media.
Only worker threads read candidate bytes, rather than loading the entire pool
of original images before starting inference.

Configuration:

- `SENTRIX_UTILITY_VLM_BASE_URL`: independent scoring endpoint; blank disables it.
- `SENTRIX_UTILITY_VLM_MODEL`: optional expected served ID; mismatch fails scoring
  explicitly rather than scoring with another model.
- `SENTRIX_UTILITY_VLM_MODEL_PATH`: launcher-only GGUF weight path, separate from
  the served model ID. Legacy launcher calls passing a `.gguf` path via MODEL
  remain accepted.
- `SENTRIX_UTILITY_VLM_CONCURRENCY`: 1 or 2; default 2.
- `SENTRIX_UTILITY_VLM_CACHE_REVISION`: default `v1`; **change this when replacing
  weights under an unchanged served model ID**. Different IDs automatically miss.

`retrieval_channels[channel=utility].all_scores` records each candidate's asset
ID, input rank, score kind/value, cache outcome (`hit`, `miss`, `coalesced`),
queue/total timings when applicable, failure reason, final selection, and ranking.
The search endpoint also returns this trace. It stays in internal tool observations,
not `model_visible`, so detailed logging does not enlarge the answering prompt.

Timing comparisons must distinguish cold and warm caches and preserve candidates,
model, QA set, and concurrency. A cache speeds repeated query/image pairs; it
cannot eliminate the first VLM inference for a new pair. Do not mix inherited
old measurements with fresh runs when reporting optimization speedups.
