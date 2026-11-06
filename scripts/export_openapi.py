#!/usr/bin/env python3
"""把 FastAPI 的 openapi.json 落到 packages/contracts/openapi/sentrix-v1.yaml。

契约是唯一真相：这个脚本产出的文件提交进仓库，前端 client 从它生成。
CI 会用 --check 比对，防止实现偷偷偏离契约。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET = REPO_ROOT / "packages" / "contracts" / "openapi" / "sentrix-v1.yaml"


def current_spec() -> dict[str, Any]:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))
    from sentrix.main import create_app

    # Schema export runs offline; runtime startup inference is tested separately.
    with (
        patch("sentrix.infrastructure.models.wemm.WeMMSidecarEmbedder.smoke_test", return_value=(True, "")),
        patch(
            "sentrix.infrastructure.models.legacy.LegacyEmbeddingHealthProbe.smoke_test",
            return_value=(True, ""),
        ),
    ):
        return create_app().openapi()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    args = parser.parse_args()

    spec = current_spec()
    rendered = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)

    if args.check:
        existing = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if existing != rendered:
            print("openapi 契约与实现不一致，请运行 scripts/export_openapi.py 更新")
            return 1
        print("openapi 契约一致")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"已写入 {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
