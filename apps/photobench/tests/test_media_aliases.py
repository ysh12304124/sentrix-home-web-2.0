"""Delivered aliases require explicit, unambiguous asset identity."""

import copy

import pytest

from photobench.domain.evaluation.media import delivered_media_aliases


def test_aliases_preserve_types_and_do_not_use_attachment_order():
    record = {
        "predicted_media": [
            {"media_type": "image", "media_id": "a.jpg", "asset_id": "i"},
            {"media_type": "video", "media_id": "a.mp4", "asset_id": "v"},
        ],
        "attachments": [
            {"handle": "video_1", "asset_id": "v"},
            {"handle": "photo_1", "asset_id": "i"},
            {"handle": "photo_1", "asset_id": "i"},
        ],
    }
    before = copy.deepcopy(record)
    assert delivered_media_aliases(record) == [
        {"handle": "video_1", "media_type": "video", "media_id": "a"},
        {"handle": "photo_1", "media_type": "image", "media_id": "a.jpg"},
    ]
    assert record == before


@pytest.mark.parametrize(
    "attachments",
    [
        [{"handle": "photo_1", "file_name": "a.jpg"}],
        [{"handle": "photo_1", "asset_id": "unknown"}],
        [{"handle": "photo_1", "asset_id": "a"}, {"handle": "photo_1", "asset_id": "b"}],
        [{"handle": "photo_1", "asset_id": "a"}, {"handle": "photo_1", "asset_id": "unknown"}],
        [{"handle": "photo_1", "asset_id": "a"}, {"handle": "photo_1"}],
    ],
)
def test_unresolved_or_conflicting_handles_are_not_guessed(attachments):
    assert (
        delivered_media_aliases(
            {
                "predicted_media": [
                    {"media_type": "image", "media_id": "a.jpg", "asset_id": "a"},
                    {"media_type": "image", "media_id": "b.jpg", "asset_id": "b"},
                ],
                "attachments": attachments,
            }
        )
        == []
    )


def test_shared_asset_cannot_prove_a_unique_media_identity():
    assert (
        delivered_media_aliases(
            {
                "predicted_media": [
                    {"media_type": "image", "media_id": "a.jpg", "asset_id": "same"},
                    {"media_type": "video", "media_id": "a", "asset_id": "same"},
                ],
                "attachments": [{"handle": "photo_1", "asset_id": "same"}],
            }
        )
        == []
    )
