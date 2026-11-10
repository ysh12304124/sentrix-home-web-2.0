"""The API keeps four import workers; Katna's one-process filter must not fork."""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

pytest.importorskip("Katna")

from Katna.image_selector import ImageSelector

from sentrix.infrastructure.media.svd_katna_scene_planner import ThreadSafeImageSelector


def test_filter_preserves_katna_thresholds_and_order_without_pool(monkeypatch):
    import Katna.image_selector

    monkeypatch.setattr(Katna.image_selector, "Pool", lambda **_: pytest.fail("forked from API thread"))
    original = ImageSelector(n_processes=1)
    images = [np.random.default_rng(i).integers(0, 256, (48, 64, 3), dtype=np.uint8) for i in range(6)]
    images.extend([np.zeros((48, 64, 3), dtype=np.uint8), np.full((48, 64, 3), 255, dtype=np.uint8)])
    expected = [
        image
        for image in images
        if (
            original.min_brightness_value
            < original.__get_brightness_score__(image)
            < original.max_brightness_value
            and original.min_entropy_value
            < original.__get_entropy_score__(image)
            < original.max_entropy_value
        )
    ]

    def run(_):
        result = ThreadSafeImageSelector(n_processes=1).__filter_optimum_brightness_and_contrast_images__(
            images
        )
        assert len(result) == len(expected)
        assert all(a is b for a, b in zip(result, expected, strict=True))
        return True

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(run, range(8)))
