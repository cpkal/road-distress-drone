import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DIRECT_PHOTO_ACCURACY_M = 3.0
OBLIQUE_ACCURACY_M = 15.0
NADIR_PITCH_DEG = -90.0
OBLIQUE_THRESHOLD_DEG = 20.0
NULL_ISLAND_EPSILON = 1e-6


class PhotoMetadataError(ValueError):
    """Raised when a photo is missing, unreadable, or not a valid JPEG image."""


@dataclass(frozen=True, slots=True)
class PhotoMetadata:
    source_file: str
    longitude: float | None
    latitude: float | None
    timestamp: float
    captured_at: datetime | None
    location_accuracy_m: float | None
    gimbal_pitch_deg: float | None
    oblique: bool
    warning: str | None = None


def _to_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _apply_hemisphere_ref(coord: float, ref: object, negative_ref: str) -> float:
    if (
        isinstance(ref, str)
        and ref.strip().upper().startswith(negative_ref)
        and coord > 0
    ):
        return -coord
    return coord


def _is_valid_lon_lat(lon: float | None, lat: float | None) -> bool:
    if lon is None or lat is None:
        return False
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        return False
    return not (abs(lon) < NULL_ISLAND_EPSILON and abs(lat) < NULL_ISLAND_EPSILON)


def _extract_lon_lat(raw: Mapping[str, object]) -> tuple[float | None, float | None]:
    # 1. Standard EXIF / Composite GPS first
    exif_lat = _to_float(
        raw.get("GPSLatitude")
        if "GPSLatitude" in raw
        else raw.get("EXIF:GPSLatitude", raw.get("GPS:GPSLatitude"))
    )
    exif_lon = _to_float(
        raw.get("GPSLongitude")
        if "GPSLongitude" in raw
        else raw.get("EXIF:GPSLongitude", raw.get("GPS:GPSLongitude"))
    )
    lat_ref = raw.get(
        "GPSLatitudeRef",
        raw.get("EXIF:GPSLatitudeRef", raw.get("GPS:GPSLatitudeRef")),
    )
    lon_ref = raw.get(
        "GPSLongitudeRef",
        raw.get("EXIF:GPSLongitudeRef", raw.get("GPS:GPSLongitudeRef")),
    )
    if exif_lat is not None and exif_lon is not None:
        lat = _apply_hemisphere_ref(exif_lat, lat_ref, "S")
        lon = _apply_hemisphere_ref(exif_lon, lon_ref, "W")
        if _is_valid_lon_lat(lon, lat):
            return lon, lat

    # 2. DJI XMP fallback (GpsLatitude / GpsLongitude)
    xmp_lat = _to_float(
        raw.get(
            "GpsLatitude",
            raw.get(
                "XMP:GpsLatitude",
                raw.get("XMP-drone-dji:GpsLatitude", raw.get("XMP:GPSLatitude")),
            ),
        )
    )
    xmp_lon = _to_float(
        raw.get(
            "GpsLongitude",
            raw.get(
                "XMP:GpsLongitude",
                raw.get("XMP-drone-dji:GpsLongitude", raw.get("XMP:GPSLongitude")),
            ),
        )
    )
    if _is_valid_lon_lat(xmp_lon, xmp_lat):
        return xmp_lon, xmp_lat

    return None, None


def _extract_gimbal_pitch(raw: Mapping[str, object]) -> tuple[float | None, bool]:
    pitch = _to_float(
        raw.get(
            "GimbalPitchDegree",
            raw.get(
                "XMP:GimbalPitchDegree", raw.get("XMP-drone-dji:GimbalPitchDegree")
            ),
        )
    )
    if pitch is None:
        return None, False
    oblique = abs(pitch - NADIR_PITCH_DEG) > OBLIQUE_THRESHOLD_DEG
    return pitch, oblique


def _parse_exif_datetime(raw: Mapping[str, object]) -> datetime | None:
    candidates = (
        raw.get("SubSecDateTimeOriginal"),
        raw.get("DateTimeOriginal"),
        raw.get("EXIF:DateTimeOriginal"),
        raw.get("CreateDate"),
        raw.get("EXIF:CreateDate"),
    )
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        text = candidate.strip()
        if not text or text.startswith("0000:00:00"):
            continue
        # Convert EXIF "YYYY:MM:DD HH:MM:SS..." date prefix to ISO "YYYY-MM-DD HH:MM:SS..."
        if len(text) >= 10 and text[4] == ":" and text[7] == ":":
            iso_text = f"{text[:4]}-{text[5:7]}-{text[8:]}"
        else:
            iso_text = text
        try:
            dt = datetime.fromisoformat(iso_text)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    return None


def parse_exiftool_record(
    raw: Mapping[str, object],
    fallback_timestamp: float = 0.0,
) -> PhotoMetadata:
    source_path = str(raw.get("SourceFile") or raw.get("FileName") or "")
    source_file = Path(source_path).name or "unknown.jpg"

    if "Error" in raw:
        raise PhotoMetadataError(f"Unreadable image '{source_file}': {raw['Error']}")

    file_type = str(raw.get("FileType") or raw.get("File:FileType") or "").upper()
    width = _to_float(raw.get("ImageWidth", raw.get("File:ImageWidth")))
    height = _to_float(raw.get("ImageHeight", raw.get("File:ImageHeight")))
    if file_type not in {"JPEG", "MPO"} or width is None or height is None:
        raise PhotoMetadataError(
            f"File '{source_file}' is not a valid JPEG image (detected type: '{file_type or 'unknown'}')."
        )

    lon, lat = _extract_lon_lat(raw)
    gimbal_pitch_deg, oblique = _extract_gimbal_pitch(raw)
    captured_at = _parse_exif_datetime(raw)
    timestamp = (
        captured_at.timestamp() if captured_at is not None else fallback_timestamp
    )

    if lon is None or lat is None:
        location_accuracy_m = None
        warning = f"No GPS metadata found in '{source_file}'."
    else:
        location_accuracy_m = OBLIQUE_ACCURACY_M if oblique else DIRECT_PHOTO_ACCURACY_M
        warning = None

    return PhotoMetadata(
        source_file=source_file,
        longitude=lon,
        latitude=lat,
        timestamp=timestamp,
        captured_at=captured_at,
        location_accuracy_m=location_accuracy_m,
        gimbal_pitch_deg=gimbal_pitch_deg,
        oblique=oblique,
        warning=warning,
    )


def run_exiftool(paths: Sequence[Path]) -> list[dict[str, object]]:
    if not paths:
        return []
    for path in paths:
        if not path.is_file():
            raise PhotoMetadataError(f"Photo file not found: '{path.name}'.")

    cmd = ["exiftool", "-j", "-n", *[str(p) for p in paths]]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except OSError as exc:
        raise PhotoMetadataError(f"Failed to execute exiftool: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PhotoMetadataError(
            "exiftool timed out while reading photo metadata."
        ) from exc

    stdout = proc.stdout.strip()
    if not stdout:
        stderr = proc.stderr.strip() or f"exiftool exited with status {proc.returncode}"
        raise PhotoMetadataError(f"Failed to read photo metadata: {stderr}")

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise PhotoMetadataError("exiftool returned invalid JSON output.") from exc

    if not isinstance(payload, list):
        raise PhotoMetadataError("Unexpected exiftool JSON structure.")

    return payload


def extract_photo_metadata(
    path: Path, fallback_timestamp: float = 0.0
) -> PhotoMetadata:
    records = run_exiftool([path])
    if not records:
        raise PhotoMetadataError(f"No metadata returned for '{path.name}'.")
    return parse_exiftool_record(records[0], fallback_timestamp=fallback_timestamp)


def extract_photos_metadata(
    paths: Sequence[Path],
) -> tuple[list[PhotoMetadata], list[str]]:
    records = run_exiftool(paths)
    if len(records) != len(paths):
        raise PhotoMetadataError(
            f"Expected metadata for {len(paths)} photo(s), got {len(records)}."
        )

    items: list[PhotoMetadata] = []
    warnings: list[str] = []
    for idx, record in enumerate(records):
        meta = parse_exiftool_record(record, fallback_timestamp=float(idx))
        items.append(meta)
        if meta.warning is not None:
            warnings.append(meta.warning)

    return items, warnings
