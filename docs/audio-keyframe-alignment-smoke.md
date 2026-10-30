# Audio and keyframe alignment smoke check

Date: 2026-10-30

## What was checked

- The `yxw` branch contains `origin/main` at `1c7ecde` as an ancestor.
- The video detail API returns the scene time range, derived keyframe timestamp, and speech segments with start/end seconds.
- The library video detail view renders the transcript segments with their time ranges and lets the user seek playback to a segment.
- The PhotoBench QA agent can retrieve the video and answer a spoken-content question from its transcript.

## Local smoke data

- Scope: `audio-align-smoke-20261030`
- Video: `cooking_paella.mp4` (`asset_64b2cafc5d09`)
- Scene: `evt_663a0e5eeecb`, source range `0.000–39.014 s`
- Derived keyframe: `asset_4d451b1b7766`, timestamp `19.507 s`
- Speech segments: `0.000–29.549 s` and `29.549–38.994 s`
- Alignment check: the keyframe timestamp falls within the first speech segment.

The existing album record above predates the SVD-default change. It has one `0–39.014 s` scene and one midpoint keyframe (`19.507 s`), matching the former fixed-window planner on this 39-second video. It is retained as the completed QA smoke input and was not reprocessed during this code change.

## Default SVD extraction check

- Ran the bundled original SVD low-rank extractor on `cooking_paella.mp4` with its checked-in configuration.
- Output: 15 scene windows and 16 selected keyframes for the 39-second video; the extractor manifest reports `svd_lowrank`.
- The selected keyframe at `19.500 s` overlaps the spoken segment `0.000–29.549 s` in the existing smoke transcript.
- `Container.build(Settings())` with `SENTRIX_VIDEO_KEYFRAME_ALGORITHM` unset selects `SVDLowRankScenePlanner`; the `window` planner remains an explicit override.

## PhotoBench QA page

- Run: `20261030-141908-audio-align-smoke-current-4c97c2`
- QA agent: completed `1/1`; terminal reason `answered`
- Video retrieval recall: `100%`
- Answer quality: `2/2`
- The answer cites the video and identifies chicken and sausage from speech.

The run is shown as **completed with errors** because the local Qwen judge twice returned output that could not be parsed for the task-action classification. The answer-quality judge returned 2/2, and the QA agent completed normally. This smoke check validates the speech-to-scene/keyframe data path and one spoken-content QA request; it does not claim broad benchmark coverage or a clean judge run.

## Checks

- API unit and contract tests: `apps/api/tests/unit/test_svd_scene_planner.py` and `apps/api/tests/contract/test_video_routes.py`
- Ruff: changed API route, schema, planner, extractor, and test files
- Web tests: `AssetDetail.test.tsx` and `ImportsPage.test.tsx` (12 tests)
- Web production build: `npm run build`
