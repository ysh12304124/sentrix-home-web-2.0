from __future__ import annotations

import numpy as np

from sentrix.application.media.cross_modal_attention import (
    attended_speech_indices,
    bidirectional_cross_modal_attention,
)


def test_bidirectional_attention_binds_speech_to_semantic_frame_across_cut() -> None:
    visual = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    audio = [[0.98, 0.02, 0.0], [0.0, 0.98, 0.02], [0.0, 0.0, 1.0]]
    result = bidirectional_cross_modal_attention(
        visual,
        audio,
        visual_times_sec=[10.0, 20.0],
        audio_times_sec=[17.0, 19.0, 20.0],
        temporal_scale_sec=45.0,
    )
    selected = attended_speech_indices(result, per_frame=1)
    assert selected[0][0][0] == 0
    assert selected[1][0][0] == 1
    assert result.visual_to_audio.shape == (2, 3)
    assert result.audio_to_visual.shape == (3, 2)
    np.testing.assert_allclose(result.visual_to_audio.sum(axis=1), 1.0)
    np.testing.assert_allclose(result.audio_to_visual.sum(axis=1), 1.0)


def test_attention_rejects_incompatible_embedding_dimensions() -> None:
    result = bidirectional_cross_modal_attention([[1.0, 0.0]], [[1.0, 0.0, 0.0]], [0.0], [0.0])
    assert result.visual_to_audio.size == 0
    assert attended_speech_indices(result) == []
