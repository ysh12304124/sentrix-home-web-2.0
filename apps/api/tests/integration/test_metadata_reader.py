from datetime import datetime

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from sentrix.infrastructure.media.metadata import PillowMetadataReader


@pytest.fixture
def jpeg_without_exif(tmp_path):
    path = tmp_path / "plain.jpg"
    Image.new("RGB", (16, 16), "white").save(path, format="JPEG")
    return path


def test_missing_exif_returns_empty(tmp_path, jpeg_without_exif):
    captured_at, gps, _ = PillowMetadataReader().read(jpeg_without_exif)

    assert captured_at is None
    assert gps is None


def test_gps_degrees_minutes_seconds_are_converted(tmp_path):
    """1.0 的度分秒转换：sum(value / 60**index)，S/W 取负。"""
    path = tmp_path / "geo.jpg"
    image = Image.new("RGB", (16, 16), "white")
    exif = image.getexif()
    exif[34853] = {  # GPSInfo；度分秒在 EXIF 里是有理数，必须用 IFDRational
        1: "N",
        2: (IFDRational(18, 1), IFDRational(47, 1), IFDRational(1788, 100)),
        3: "E",
        4: (IFDRational(98, 1), IFDRational(59, 1), IFDRational(700, 100)),
    }
    image.save(path, format="JPEG", exif=exif)

    captured_at, gps, _ = PillowMetadataReader().read(path)

    assert gps is not None
    assert gps.latitude == pytest.approx(18.7883, abs=1e-4)
    assert gps.longitude == pytest.approx(98.9853, abs=1e-4)


def test_southern_and_western_hemispheres_are_negated(tmp_path):
    path = tmp_path / "south.jpg"
    image = Image.new("RGB", (16, 16), "white")
    exif = image.getexif()
    exif[34853] = {
        1: "S",
        2: (IFDRational(33, 1), IFDRational(52, 1), IFDRational(0, 1)),
        3: "W",
        4: (IFDRational(151, 1), IFDRational(12, 1), IFDRational(0, 1)),
    }
    image.save(path, format="JPEG", exif=exif)

    _, gps, _ = PillowMetadataReader().read(path)

    assert gps.latitude == pytest.approx(-33.8667, abs=1e-3)
    assert gps.longitude == pytest.approx(-151.2, abs=1e-3)


def test_datetime_original_is_normalised_to_iso(tmp_path):
    path = tmp_path / "dated.jpg"
    image = Image.new("RGB", (16, 16), "white")
    exif = image.getexif()
    exif[36867] = "2018:04:01 21:05:47"  # DateTimeOriginal
    image.save(path, format="JPEG", exif=exif)

    captured_at, _, _ = PillowMetadataReader().read(path)

    assert captured_at == datetime(2018, 4, 1, 21, 5, 47)


def test_corrupt_file_degrades_to_empty(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not an image at all")

    captured_at, gps, _ = PillowMetadataReader().read(path)

    assert captured_at is None
    assert gps is None


def test_device_prefers_model_over_make(tmp_path):
    """设备型号来自 EXIF；Make(271)/Model(272) 都在时取 Model（device = model or make）。"""
    path = tmp_path / "device.jpg"
    image = Image.new("RGB", (16, 16), "white")
    exif = image.getexif()
    exif[271] = "Apple"  # Make
    exif[272] = "iPhone 12"  # Model
    image.save(path, format="JPEG", exif=exif)

    _, _, device = PillowMetadataReader().read(path)

    assert device == "iPhone 12"


def test_nested_capture_ifd_preserves_original_offset_across_midnight(tmp_path):
    from datetime import timedelta

    path = tmp_path / "nested.jpg"
    image = Image.new("RGB", (16, 16), "white")
    exif = image.getexif()
    exif[306] = "2024:01:02 01:00:00"
    exif[34665] = {36867: "2024:01:01 23:30:00", 36881: "-05:00", 36880: "+08:00"}
    image.save(path, format="JPEG", exif=exif)
    captured_at, _, _ = PillowMetadataReader().read(path)
    assert captured_at.isoformat() == "2024-01-01T23:30:00-05:00"
    assert captured_at.utcoffset() == timedelta(hours=-5)


def test_original_without_offset_does_not_borrow_modified_timezone(tmp_path):
    path = tmp_path / "unknown-zone.jpg"
    image = Image.new("RGB", (16, 16), "white")
    exif = image.getexif()
    exif[34665] = {36867: "2024:01:01 23:30:00", 36880: "+08:00"}
    image.save(path, format="JPEG", exif=exif)
    captured_at, _, _ = PillowMetadataReader().read(path)
    assert captured_at == datetime(2024, 1, 1, 23, 30)
    assert captured_at.tzinfo is None
