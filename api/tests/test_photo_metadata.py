import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.photo_metadata import (
    DIRECT_PHOTO_ACCURACY_M,
    OBLIQUE_ACCURACY_M,
    PhotoMetadataError,
    extract_photo_metadata,
    extract_photos_metadata,
    parse_exiftool_record,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PHOTO_WITH_GPS = FIXTURES_DIR / "photo_with_gps.jpg"
PHOTO_WITHOUT_GPS = FIXTURES_DIR / "photo_without_gps.jpg"


def test_extract_photo_with_gps_fixture() -> None:
    meta = extract_photo_metadata(PHOTO_WITH_GPS)
    assert meta.source_file == "photo_with_gps.jpg"
    assert meta.longitude == pytest.approx(100.501762, abs=1e-6)
    assert meta.latitude == pytest.approx(13.756331, abs=1e-6)
    assert meta.gimbal_pitch_deg == pytest.approx(-88.5)
    assert meta.oblique is False
    assert meta.location_accuracy_m == DIRECT_PHOTO_ACCURACY_M
    assert meta.captured_at == datetime(2026, 10, 28, 10, 15, 30, tzinfo=UTC)
    assert meta.timestamp == meta.captured_at.timestamp()
    assert meta.warning is None


def test_extract_photo_without_gps_fixture() -> None:
    meta = extract_photo_metadata(PHOTO_WITHOUT_GPS, fallback_timestamp=4.0)
    assert meta.source_file == "photo_without_gps.jpg"
    assert meta.longitude is None
    assert meta.latitude is None
    assert meta.location_accuracy_m is None
    assert meta.gimbal_pitch_deg is None
    assert meta.oblique is False
    assert meta.captured_at is None
    assert meta.timestamp == 4.0
    assert meta.warning is not None
    assert "photo_without_gps.jpg" in meta.warning


def test_extract_photos_batch_and_warnings() -> None:
    items, warnings = extract_photos_metadata([PHOTO_WITH_GPS, PHOTO_WITHOUT_GPS])
    assert len(items) == 2
    assert items[0].longitude == pytest.approx(100.501762, abs=1e-6)
    assert items[1].longitude is None
    assert len(warnings) == 1
    assert "photo_without_gps.jpg" in warnings[0]


def test_dji_xmp_fallback_and_oblique_pitch_with_exiftool(tmp_path: Path) -> None:
    xmp_photo = tmp_path / "dji_oblique.jpg"
    shutil.copyfile(PHOTO_WITHOUT_GPS, xmp_photo)
    subprocess.run(
        [
            "exiftool",
            "-overwrite_original",
            "-XMP-drone-dji:GpsLatitude=-6.2088",
            "-XMP-drone-dji:GpsLongitude=106.8456",
            "-XMP-drone-dji:GimbalPitchDegree=-60.0",
            str(xmp_photo),
        ],
        check=True,
        capture_output=True,
    )

    meta = extract_photo_metadata(xmp_photo)
    assert meta.longitude == pytest.approx(106.8456, abs=1e-6)
    assert meta.latitude == pytest.approx(-6.2088, abs=1e-6)
    assert meta.gimbal_pitch_deg == pytest.approx(-60.0)
    assert meta.oblique is True
    assert meta.location_accuracy_m == OBLIQUE_ACCURACY_M
    assert meta.warning is None


def test_parse_record_dji_xmp_fallback_when_exif_missing_or_zero() -> None:
    record = {
        "SourceFile": "dji_frame.jpg",
        "FileType": "JPEG",
        "ImageWidth": 1920,
        "ImageHeight": 1080,
        "GPSLatitude": 0.0,
        "GPSLongitude": 0.0,
        "GpsLatitude": "35.6895",
        "GpsLongitude": "139.6917",
        "GimbalPitchDegree": "-90.0",
    }
    meta = parse_exiftool_record(record)
    assert meta.longitude == pytest.approx(139.6917)
    assert meta.latitude == pytest.approx(35.6895)
    assert meta.oblique is False
    assert meta.location_accuracy_m == DIRECT_PHOTO_ACCURACY_M
    assert meta.warning is None


def test_parse_record_null_island_treated_as_missing_gps() -> None:
    record = {
        "SourceFile": "no_lock.jpg",
        "FileType": "JPEG",
        "ImageWidth": 1920,
        "ImageHeight": 1080,
        "GPSLatitude": 0.0,
        "GPSLongitude": 0.0,
    }
    meta = parse_exiftool_record(record, fallback_timestamp=2.0)
    assert meta.longitude is None
    assert meta.latitude is None
    assert meta.location_accuracy_m is None
    assert meta.warning is not None


def test_parse_record_southern_and_western_hemisphere_refs() -> None:
    record = {
        "SourceFile": "sydney_west.jpg",
        "FileType": "JPEG",
        "ImageWidth": 1920,
        "ImageHeight": 1080,
        "EXIF:GPSLatitude": 33.8688,
        "EXIF:GPSLatitudeRef": "South",
        "EXIF:GPSLongitude": 151.2093,
        "EXIF:GPSLongitudeRef": "West",
    }
    meta = parse_exiftool_record(record)
    assert meta.latitude == pytest.approx(-33.8688)
    assert meta.longitude == pytest.approx(-151.2093)


def test_corrupt_or_non_jpeg_photo_raises_error(tmp_path: Path) -> None:
    corrupt_path = tmp_path / "corrupt.jpg"
    corrupt_path.write_bytes(b"not a valid jpeg image")

    with pytest.raises(PhotoMetadataError, match="not a valid JPEG"):
        extract_photo_metadata(corrupt_path)


def test_missing_photo_file_raises_error(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.jpg"
    with pytest.raises(PhotoMetadataError, match="not found"):
        extract_photo_metadata(missing)
