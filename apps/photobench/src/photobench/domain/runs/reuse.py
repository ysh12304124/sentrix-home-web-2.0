"""``GET /api/memory-spaces`` 的「可复用基座」（1.0 ``_build_reuse_bases`` ``:1080``）。

把每个 run 上记着的 ``scope_id`` / ``album_id`` / ``model_profile`` 与 Sentrix 的 space
关联成「这个相册 × 这个模型已经建好、可以直接复用」的几组，供前端的选择器直接用。

**它落在 domain 因为它纯**：spaces 与 run 条目都由调用方取回来
（``application/spaces/service.py`` 拿 ``RunIndex`` 的条目），这里不碰文件与网络。
与 ``analysis.py`` / ``execution.py`` 同一条分工。

行号注释指 1.0 ``services/photobench/backend/benchmark_orchestrator.py``。
"""

from __future__ import annotations

import re
from typing import Any

from photobench.domain.runs.execution import safe_slug


def build_reuse_bases(spaces: list[dict[str, Any]], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1.0 ``:1080`` 逐行照搬。

    四件容易写错的事，都照 1.0：

    1. **同一个 scope 上挂多个 run 时取最新那个**（``started_at`` 优先、退 ``created_at``，
       逆序）——它决定 ``album_id`` 与 ``source_run_ids``。
    2. ``model_profile`` 先按「模型 slug 出现在 space 名字里」挑，挑不到才回落到
       run 上记的那个；**候选按长度逆序**，所以 ``big_model`` 不会被 ``model`` 抢先命中。
    3. 两边都没拿到时，回落到 space 名字里 ``PhotoBench-YYYYMMDD-HHMMSS-<album>-<model>``
       这条历史命名（1.0 ``:1118`` 的正则，模型名限定在几个已知前缀里）。
    4. ``ready`` 只看 ``space.status`` 是不是 ``"active"``（缺省也算 active）。
    """
    runs_by_scope: dict[str, list[dict[str, Any]]] = {}
    for run in runs or []:
        if not isinstance(run, dict) or not run.get("scope_id"):
            continue
        runs_by_scope.setdefault(str(run["scope_id"]), []).append(run)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for space in spaces or []:
        if not isinstance(space, dict) or not space.get("id"):
            continue
        scope_id = str(space["id"])
        linked = sorted(
            runs_by_scope.get(scope_id, []),
            key=lambda item: str(item.get("started_at") or item.get("created_at") or ""),
            reverse=True,
        )
        source = linked[0] if linked else {}
        build = next((run["scope_build"] for run in linked if run.get("scope_build")), None)
        album_id = str(source.get("album_id") or "").strip()
        model_candidates = sorted(
            {str(run.get("model_profile") or "").strip() for run in linked if run.get("model_profile")},
            key=len,
            reverse=True,
        )
        space_name = str(space.get("name") or "").lower()
        model_profile = next(
            (model for model in model_candidates if safe_slug(model).lower() in space_name), ""
        )
        model_profile = model_profile or str(source.get("model_profile") or "").strip()
        if not album_id or not model_profile:
            name = str(space.get("name") or "")
            match = re.search(
                r"PhotoBench-\d{8}-\d{6}-(?P<album>.+?)-"
                r"(?P<model>(?:qwen|gemma|llama|phi|mistral|current|big_model)[^-]*)$",
                name,
                re.I,
            )
            if match:
                album_id = album_id or match.group("album")
                model_profile = model_profile or match.group("model")
        if not album_id or not model_profile:
            continue
        key = (album_id, model_profile)
        matching_runs = [
            run
            for run in linked
            if str(run.get("album_id") or "") == album_id
            and str(run.get("model_profile") or "") == model_profile
        ]
        candidate = {
            "base_id": f"{album_id}::{model_profile}",
            "album_id": album_id,
            "model_profile": model_profile,
            "scope_id": scope_id,
            "scope_name": space.get("name") or scope_id,
            "created_at": space.get("created_at") or "",
            "source_run_ids": sorted(str(run["run_id"]) for run in matching_runs if run.get("run_id")),
            "ready": str(space.get("status") or "active") == "active"
            and bool(linked)
            and (build is None or build.get("status") == "ready"),
        }
        if build is not None:
            candidate["scope_build"] = build
        group = groups.get(key)
        # Select one whole scope snapshot. Never attach an older scope's ready
        # marker or source runs to a newer, incomplete scope.
        if group is None or (bool(candidate["ready"]), str(candidate["created_at"])) > (
            bool(group["ready"]),
            str(group["created_at"]),
        ):
            groups[key] = candidate
    return sorted(
        groups.values(),
        key=lambda item: (str(item.get("album_id") or ""), str(item.get("model_profile") or "")),
    )
