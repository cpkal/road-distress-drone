import bisect
import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

EXACT_TELEMETRY_ACCURACY_M = 3.0
INTERPOLATED_ACCURACY_M = 5.0
OBLIQUE_ACCURACY_M = 15.0
NADIR_PITCH_DEG = -90.0
OBLIQUE_THRESHOLD_DEG = 20.0
NULL_ISLAND_EPSILON = 1e-6
EXACT_TIME_EPSILON_S = 1e-3
DEFAULT_CLAMP_WINDOW_S = 1.0

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SRT_TIMECODE_RE = re.compile(
    r"(?P<sh>\d{1,2}):(?P<sm>\d{2}):(?P<ss>\d{2})[,.](?P<sms>\d{1,3})\s*-->\s*"
    r"(?P<eh>\d{1,2}):(?P<em>\d{2}):(?P<es>\d{2})[,.](?P<ems>\d{1,3})"
)
_LAT_KV_RE = re.compile(
    r"\b(?:latitude|lat)\s*[:=]\s*(?P<val>[+-]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_LON_KV_RE = re.compile(
    r"\b(?:longitude|longtitude|lon|lng)\s*[:=]\s*(?P<val>[+-]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_GPS_TUPLE_RE = re.compile(
    r"\bGPS\s*\(\s*(?P<a>[+-]?\d+(?:\.\d+)?)\s*,\s*(?P<b>[+-]?\d+(?:\.\d+)?)"
    r"(?:\s*,\s*[^)]*)?\)",
    re.IGNORECASE,
)
_PITCH_KV_RE = re.compile(
    r"\b(?:gimbal_pitch|gb_pitch|gimbalpitchdegree|pitch)\s*[:=]\s*(?P<val>[+-]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


class VideoTelemetryError(ValueError):
    """Raised when a video file is missing, corrupt, or has no valid video stream."""


@dataclass(frozen=True, slots=True)
class TelemetrySample:
    timestamp: float
    longitude: float
    latitude: float
    gimbal_pitch_deg: float | None = None


@dataclass(frozen=True, slots=True)
class InterpolatedPosition:
    timestamp: float
    longitude: float | None
    latitude: float | None
    location_accuracy_m: float | None
    gimbal_pitch_deg: float | None
    oblique: bool


@dataclass(frozen=True, slots=True)
class VideoTelemetryResult:
    samples: list[TelemetrySample]
    duration_s: float
    warning: str | None = None


def _is_valid_lon_lat(lon: float, lat: float) -> bool:
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        return False
    return not (abs(lon) < NULL_ISLAND_EPSILON and abs(lat) < NULL_ISLAND_EPSILON)


def _is_oblique(gimbal_pitch_deg: float | None) -> bool:
    if gimbal_pitch_deg is None:
        return False
    return abs(gimbal_pitch_deg - NADIR_PITCH_DEG) > OBLIQUE_THRESHOLD_DEG


def _parse_timecode_seconds(
    hours: str, minutes: str, seconds: str, millis: str
) -> float:
    ms_normalized = millis.ljust(3, "0")[:3]
    return (
        int(hours) * 3600.0
        + int(minutes) * 60.0
        + int(seconds)
        + int(ms_normalized) / 1000.0
    )


def _extract_coords_from_text(text: str) -> tuple[float, float] | None:
    lat_match = _LAT_KV_RE.search(text)
    lon_match = _LON_KV_RE.search(text)
    if lat_match and lon_match:
        try:
            lat = float(lat_match.group("val"))
            lon = float(lon_match.group("val"))
        except ValueError:
            return None
        if _is_valid_lon_lat(lon, lat):
            return lon, lat
        return None

    gps_match = _GPS_TUPLE_RE.search(text)
    if gps_match:
        try:
            a = float(gps_match.group("<a>" if "<a>" in gps_match.groupdict() else "a"))
            b = float(gps_match.group("b"))
        except ValueError:
            return None
        # DJI GPS(lon, lat, alt) places longitude first; if |b| > 90 and |a| <= 90, swap.
        lon, lat = (b, a) if (abs(b) > 90.0 and abs(a) <= 90.0) else (a, b)
        if _is_valid_lon_lat(lon, lat):
            return lon, lat

    return None


def _extract_pitch_from_text(text: str) -> float | None:
    match = _PITCH_KV_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group("val"))
    except ValueError:
        return None


def parse_srt_telemetry(srt_text: str) -> list[TelemetrySample]:
    normalized = srt_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    by_timestamp: dict[float, TelemetrySample] = {}
    blocks = re.split(r"\n\s*\n", normalized)
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if len(lines) < 2:
            continue

        timecode_match = None
        payload_start_idx = 0
        for idx, line in enumerate(lines):
            m = _SRT_TIMECODE_RE.search(line)
            if m is not None:
                timecode_match = m
                payload_start_idx = idx + 1
                break

        if timecode_match is None or payload_start_idx >= len(lines):
            continue

        timestamp = _parse_timecode_seconds(
            timecode_match.group("sh"),
            timecode_match.group("sm"),
            timecode_match.group("ss"),
            timecode_match.group("sms"),
        )
        raw_payload = " ".join(lines[payload_start_idx:])
        clean_payload = _HTML_TAG_RE.sub(" ", raw_payload)

        coords = _extract_coords_from_text(clean_payload)
        if coords is None:
            continue

        lon, lat = coords
        pitch = _extract_pitch_from_text(clean_payload)
        by_timestamp[timestamp] = TelemetrySample(
            timestamp=timestamp,
            longitude=lon,
            latitude=lat,
            gimbal_pitch_deg=pitch,
        )

    return [by_timestamp[t] for t in sorted(by_timestamp)]


def probe_video(video_path: Path) -> tuple[float, list[int]]:
    if not video_path.is_file():
        raise VideoTelemetryError(f"Video file not found: '{video_path.name}'.")

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-show_entries",
        "stream=index,codec_type,duration",
        "-of",
        "json",
        str(video_path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=30
        )
    except OSError as exc:
        raise VideoTelemetryError(f"Failed to run ffprobe: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoTelemetryError(f"ffprobe timed out on '{video_path.name}'.") from exc

    if proc.returncode != 0 or not proc.stdout.strip():
        stderr = proc.stderr.strip() or f"ffprobe exited with code {proc.returncode}"
        raise VideoTelemetryError(
            f"Corrupt or unreadable video '{video_path.name}': {stderr}"
        )

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise VideoTelemetryError(
            f"Invalid ffprobe output for '{video_path.name}'."
        ) from exc

    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise VideoTelemetryError(f"No media streams found in '{video_path.name}'.")

    video_streams = [
        s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"
    ]
    if not video_streams:
        raise VideoTelemetryError(
            f"File '{video_path.name}' does not contain a video stream."
        )

    subtitle_indices = [
        int(s["index"])
        for s in streams
        if isinstance(s, dict)
        and s.get("codec_type") == "subtitle"
        and isinstance(s.get("index"), int)
    ]

    duration_s = 0.0
    fmt = payload.get("format")
    if isinstance(fmt, dict) and fmt.get("duration") is not None:
        try:
            duration_s = max(0.0, float(fmt["duration"]))
        except TypeError, ValueError:
            duration_s = 0.0

    if duration_s == 0.0:
        for vs in video_streams:
            if vs.get("duration") is not None:
                try:
                    duration_s = max(0.0, float(vs["duration"]))
                    break
                except TypeError, ValueError:
                    continue

    return duration_s, subtitle_indices


def extract_embedded_srt(video_path: Path, stream_index: int) -> str:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(video_path),
        "-map",
        f"0:{stream_index}",
        "-f",
        "srt",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=60
        )
    except OSError, subprocess.TimeoutExpired:
        return ""

    if proc.returncode != 0:
        return ""
    return proc.stdout


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="replace")


def load_video_telemetry(
    video_path: Path,
    srt_path: Path | None = None,
) -> VideoTelemetryResult:
    duration_s, subtitle_indices = probe_video(video_path)

    srt_text = ""
    if srt_path is not None and srt_path.is_file():
        srt_text = _read_text_file(srt_path)
    elif subtitle_indices:
        for stream_idx in subtitle_indices:
            extracted = extract_embedded_srt(video_path, stream_idx)
            if extracted.strip():
                srt_text = extracted
                break

    samples = parse_srt_telemetry(srt_text) if srt_text.strip() else []
    warning = (
        None if samples else f"No GPS telemetry found for video '{video_path.name}'."
    )
    return VideoTelemetryResult(samples=samples, duration_s=duration_s, warning=warning)


def _position_from_sample(
    sample: TelemetrySample,
    timestamp: float,
    is_exact: bool,
) -> InterpolatedPosition:
    oblique = _is_oblique(sample.gimbal_pitch_deg)
    if oblique:
        accuracy = OBLIQUE_ACCURACY_M
    elif is_exact:
        accuracy = EXACT_TELEMETRY_ACCURACY_M
    else:
        accuracy = INTERPOLATED_ACCURACY_M

    return InterpolatedPosition(
        timestamp=timestamp,
        longitude=sample.longitude,
        latitude=sample.latitude,
        location_accuracy_m=accuracy,
        gimbal_pitch_deg=sample.gimbal_pitch_deg,
        oblique=oblique,
    )


def interpolate_position(
    samples: Sequence[TelemetrySample],
    timestamp: float,
    max_clamp_s: float = DEFAULT_CLAMP_WINDOW_S,
) -> InterpolatedPosition:
    if not samples:
        return InterpolatedPosition(
            timestamp=timestamp,
            longitude=None,
            latitude=None,
            location_accuracy_m=None,
            gimbal_pitch_deg=None,
            oblique=False,
        )

    first = samples[0]
    if timestamp <= first.timestamp:
        diff = first.timestamp - timestamp
        if diff <= max_clamp_s:
            return _position_from_sample(
                first, timestamp, is_exact=diff <= EXACT_TIME_EPSILON_S
            )
        return InterpolatedPosition(
            timestamp=timestamp,
            longitude=None,
            latitude=None,
            location_accuracy_m=None,
            gimbal_pitch_deg=None,
            oblique=False,
        )

    last = samples[-1]
    if timestamp >= last.timestamp:
        diff = timestamp - last.timestamp
        if diff <= max_clamp_s:
            return _position_from_sample(
                last, timestamp, is_exact=diff <= EXACT_TIME_EPSILON_S
            )
        return InterpolatedPosition(
            timestamp=timestamp,
            longitude=None,
            latitude=None,
            location_accuracy_m=None,
            gimbal_pitch_deg=None,
            oblique=False,
        )

    timestamps = [s.timestamp for s in samples]
    idx = bisect.bisect_right(timestamps, timestamp)
    s0 = samples[idx - 1]
    s1 = samples[idx]

    if abs(timestamp - s0.timestamp) <= EXACT_TIME_EPSILON_S:
        return _position_from_sample(s0, timestamp, is_exact=True)
    if abs(s1.timestamp - timestamp) <= EXACT_TIME_EPSILON_S:
        return _position_from_sample(s1, timestamp, is_exact=True)

    span = s1.timestamp - s0.timestamp
    alpha = (timestamp - s0.timestamp) / span if span > 0 else 0.0
    lon = s0.longitude + alpha * (s1.longitude - s0.longitude)
    lat = s0.latitude + alpha * (s1.latitude - s0.latitude)

    if s0.gimbal_pitch_deg is not None and s1.gimbal_pitch_deg is not None:
        pitch: float | None = s0.gimbal_pitch_deg + alpha * (
            s1.gimbal_pitch_deg - s0.gimbal_pitch_deg
        )
    elif s0.gimbal_pitch_deg is not None:
        pitch = s0.gimbal_pitch_deg
    else:
        pitch = s1.gimbal_pitch_deg

    oblique = _is_oblique(pitch)
    accuracy = OBLIQUE_ACCURACY_M if oblique else INTERPOLATED_ACCURACY_M

    return InterpolatedPosition(
        timestamp=timestamp,
        longitude=lon,
        latitude=lat,
        location_accuracy_m=accuracy,
        gimbal_pitch_deg=pitch,
        oblique=oblique,
    )
