#!/usr/bin/env python3
"""输出当前图片观察 profile 的机器可读矩阵；不记录任何密钥。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from sentrix.config import Settings
from sentrix.infrastructure.media.vlm_observer import IMAGE_MAX_SIDE


@dataclass(frozen=True, slots=True)
class ImageProfile:
    deployment_profile: str
    llm_profile: str
    vlm_base_url: str
    vlm_model: str
    vlm_generation_profile: str
    vlm_context_tokens: int
    vlm_exact_output_budget: bool
    image_max_side: int | None
    embedding_scheme: str


def main() -> None:
    settings = Settings()
    profile = ImageProfile(
        deployment_profile=settings.deployment_profile,
        llm_profile=settings.llm_profile,
        vlm_base_url=settings.vlm_base_url,
        vlm_model=settings.vlm_model,
        vlm_generation_profile=settings.vlm_generation_profile,
        vlm_context_tokens=settings.vlm_context_tokens,
        vlm_exact_output_budget=settings.vlm_exact_output_budget,
        image_max_side=IMAGE_MAX_SIDE,
        embedding_scheme=settings.embedding_scheme,
    )
    print(json.dumps(asdict(profile), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
