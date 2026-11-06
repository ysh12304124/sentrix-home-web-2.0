import pytest

from sentrix.application.imports import UploadRoute, classify_upload


@pytest.mark.parametrize(
    ("file_name", "content_type", "content", "expected"),
    [
        ("notes.txt", "text/plain", b"hello", UploadRoute.DOCUMENT),
        ("report.docx", "application/octet-stream", b"PK\x03\x04", UploadRoute.DOCUMENT),
        ("paper.pdf", "application/pdf", b"%PDF-1.7", UploadRoute.DOCUMENT),
        ("photo.jpg", "image/jpeg", b"jpeg", UploadRoute.MEDIA),
        ("voice.mp3", "audio/mpeg", b"audio", UploadRoute.MEDIA),
        ("clip.mp4", "video/mp4", b"video", UploadRoute.MEDIA),
        ("upload", "image/png", b"png", UploadRoute.MEDIA),
    ],
)
def test_classify_upload(
    file_name: str,
    content_type: str,
    content: bytes,
    expected: UploadRoute,
) -> None:
    assert classify_upload(file_name, content_type, content) is expected


@pytest.mark.parametrize(
    ("file_name", "content_type", "content"),
    [
        ("paper.pdf", "image/jpeg", b"%PDF-1.7"),
        ("photo.jpg", "application/pdf", b"%PDF-1.7"),
        ("voice.mp3", "video/mp4", b"media"),
        ("archive.zip", "application/octet-stream", b"PK\x03\x04"),
    ],
)
def test_classify_upload_rejects_conflicts_and_unknowns(
    file_name: str, content_type: str, content: bytes
) -> None:
    with pytest.raises(ValueError):
        classify_upload(file_name, content_type, content)
