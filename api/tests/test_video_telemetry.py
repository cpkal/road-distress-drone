import subprocess
from pathlib import Path

import pytest

from app.services.video_telemetry import (
    EXACT_TELEMETRY_ACCURACY_M,
    INTERPOLATED_ACCURACY_M,
    OBLIQUE_ACCURACY_M,
    TelemetrySample,
    VideoTelemetryError,
    interpolate_position,
    load_video_telemetry,
    parse_srt_telemetry,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_VIDEO = FIXTURES_DIR / "sample_video.mp4"
SAMPLE_SRT = FIXTURES_DIR / "sample_video.srt"


def test_load_video_telemetry_from_sidecar_srt() -> None:
    result = load_video_telemetry(SAMPLE_VIDEO, SAMPLE_SRT)
    assert result.duration_s == pytest.approx(2.5, abs=0.1)
    assert result.warning is None
    assert len(result.samples) == 3

    assert result.samples[0].timestamp == pytest.approx(0.0)
    assert result.samples[0].longitude == pytest.approx(100.501700)
    assert result.samples[0].latitude == pytest.approx(13.756300)
    assert result.samples[0].gimbal_pitch_deg == pytest.approx(-89.0)

    assert result.samples[2].timestamp == pytest.approx(2.0)
    assert result.samples[2].longitude == pytest.approx(100.501900)
    assert result.samples[2].latitude == pytest.approx(13.756500)


def test_load_video_telemetry_from_embedded_subtitle_stream(tmp_path: Path) -> None:
    embedded_video = tmp_path / "with_embedded_sub.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(SAMPLE_VIDEO),
            "-i",
            str(SAMPLE_SRT),
            "-c:v",
            "copy",
            "-c:s",
            "mov_text",
            str(embedded_video),
        ],
        check=True,
        capture_output=True,
    )

    result = load_video_telemetry(embedded_video, srt_path=None)
    assert result.warning is None
    assert len(result.samples) == 3
    assert result.samples[1].timestamp == pytest.approx(1.0)
    assert result.samples[1].longitude == pytest.approx(100.501800)
    assert result.samples[1].latitude == pytest.approx(13.756400)


def test_load_video_telemetry_without_any_subtitles_returns_warning() -> None:
    result = load_video_telemetry(SAMPLE_VIDEO, srt_path=None)
    assert result.samples == []
    assert result.warning is not None
    assert "sample_video.mp4" in result.warning


def test_parse_srt_variants_and_ignore_null_island() -> None:
    srt_content = """1
00:00:00,000 --> 00:00:01,000
F/2.8, GPS (0.0000, 0.0000, 0)

2
00:00:01,000 --> 00:00:02,000
F/2.8, GPS (100.5100, 13.7600, 18.5M) [pitch: -85.0]

3
00:00:02.500 --> 00:00:03.500
<font size="28">[latitude : -6.2088] [longtitude : 106.8456] [gb_pitch : -55.0]</font>
"""
    samples = parse_srt_telemetry(srt_content)
    assert len(samples) == 2

    assert samples[0].timestamp == pytest.approx(1.0)
    assert samples[0].longitude == pytest.approx(100.5100)
    assert samples[0].latitude == pytest.approx(13.7600)
    assert samples[0].gimbal_pitch_deg == pytest.approx(-85.0)

    assert samples[1].timestamp == pytest.approx(2.5)
    assert samples[1].longitude == pytest.approx(106.8456)
    assert samples[1].latitude == pytest.approx(-6.2088)
    assert samples[1].gimbal_pitch_deg == pytest.approx(-55.0)


def test_interpolate_position_exact_midpoint_and_clamping() -> None:
    samples = [
        TelemetrySample(
            timestamp=1.0, longitude=100.0, latitude=10.0, gimbal_pitch_deg=-90.0
        ),
        TelemetrySample(
            timestamp=3.0, longitude=102.0, latitude=14.0, gimbal_pitch_deg=-80.0
        ),
    ]

    # Exact timestamp match
    exact = interpolate_position(samples, timestamp=1.0)
    assert exact.longitude == pytest.approx(100.0)
    assert exact.latitude == pytest.approx(10.0)
    assert exact.gimbal_pitch_deg == pytest.approx(-90.0)
    assert exact.oblique is False
    assert exact.location_accuracy_m == EXACT_TELEMETRY_ACCURACY_M

    # Midpoint linear interpolation
    mid = interpolate_position(samples, timestamp=2.0)
    assert mid.longitude == pytest.approx(101.0)
    assert mid.latitude == pytest.approx(12.0)
    assert mid.gimbal_pitch_deg == pytest.approx(-85.0)
    assert mid.oblique is False
    assert mid.location_accuracy_m == INTERPOLATED_ACCURACY_M

    # Clamped within 1.0s before first sample
    clamped_start = interpolate_position(samples, timestamp=0.2)
    assert clamped_start.longitude == pytest.approx(100.0)
    assert clamped_start.latitude == pytest.approx(10.0)
    assert clamped_start.location_accuracy_m == INTERPOLATED_ACCURACY_M

    # Clamped within 1.0s after last sample
    clamped_end = interpolate_position(samples, timestamp=3.8)
    assert clamped_end.longitude == pytest.approx(102.0)
    assert clamped_end.latitude == pytest.approx(14.0)
    assert clamped_end.location_accuracy_m == INTERPOLATED_ACCURACY_M

    # Outside 1.0s clamp window -> null location
    out_of_range = interpolate_position(samples, timestamp=4.5)
    assert out_of_range.longitude is None
    assert out_of_range.latitude is None
    assert out_of_range.location_accuracy_m is None


def test_interpolate_position_oblique_pitch_sets_lower_accuracy() -> None:
    samples = [
        TelemetrySample(
            timestamp=0.0, longitude=100.0, latitude=13.0, gimbal_pitch_deg=-60.0
        ),
        TelemetrySample(
            timestamp=1.0, longitude=100.1, latitude=13.1, gimbal_pitch_deg=-50.0
        ),
    ]
    pos = interpolate_position(samples, timestamp=0.5)
    assert pos.gimbal_pitch_deg == pytest.approx(-55.0)
    assert pos.oblique is True
    assert pos.location_accuracy_m == OBLIQUE_ACCURACY_M


def test_corrupt_video_raises_error(tmp_path: Path) -> None:
    corrupt_video = tmp_path / "corrupt.mp4"
    corrupt_video.write_bytes(b"not a valid mp4 file")

    with pytest.raises(VideoTelemetryError, match="Corrupt or unreadable video"):
        load_video_telemetry(corrupt_video)
