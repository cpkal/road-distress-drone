import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from geoalchemy2 import WKTElement
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import get_session_factory
from app.models.upload import Detection, Frame, Upload, UploadKind, UploadStatus
from app.services.detector import (
    DetectorProtocol,
    FrameCandidate,
    UltralyticsYoloDetector,
    ValidatedDetection,
    deduplicate_detections,
    validate_predictions,
)
from app.services.photo_metadata import extract_photos_metadata
from app.services.uploads import PHOTO_EXTENSIONS, SRT_EXTENSIONS, VIDEO_EXTENSIONS
from app.services.video_telemetry import (
    VideoTelemetryError,
    interpolate_position,
    load_video_telemetry,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _SampledFrameSpec:
    source_file: str
    timestamp: float
    longitude: float | None
    latitude: float | None
    location_accuracy_m: float | None
    gimbal_pitch_deg: float | None
    oblique: bool
    temp_image_path: Path


def _make_point(lon: float | None, lat: float | None) -> WKTElement | None:
    if lon is None or lat is None:
        return None
    # PostGIS WKT POINT uses (longitude latitude) order in EPSG:4326.
    return WKTElement(f"POINT({lon} {lat})", srid=4326)


def sample_video_frames(
    video_path: Path,
    output_dir: Path,
    sample_fps: float,
) -> list[tuple[float, Path]]:
    if sample_fps <= 0:
        raise ValueError("FRAME_SAMPLE_FPS must be positive.")

    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / "sample_%06d.jpg"

    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(video_path),
        "-vf",
        f"fps={sample_fps}",
        "-q:v",
        "2",
        str(pattern),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=300
        )
    except OSError as exc:
        raise VideoTelemetryError(f"Failed to execute ffmpeg: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoTelemetryError(
            f"ffmpeg timed out while sampling '{video_path.name}'."
        ) from exc

    if proc.returncode != 0:
        stderr = proc.stderr.strip() or f"ffmpeg exited with code {proc.returncode}"
        raise VideoTelemetryError(
            f"Failed to decode video '{video_path.name}': {stderr}"
        )

    extracted = sorted(output_dir.glob("sample_*.jpg"))

    # Videos shorter than 1/sample_fps still sample at least frame t=0.0
    if not extracted:
        fallback_path = output_dir / "sample_000001.jpg"
        fallback_cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(fallback_path),
        ]
        try:
            proc_fb = subprocess.run(
                fallback_cmd, capture_output=True, text=True, check=False, timeout=60
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VideoTelemetryError(
                f"Failed to extract initial frame from '{video_path.name}': {exc}"
            ) from exc

        if proc_fb.returncode != 0 or not fallback_path.is_file():
            stderr = proc_fb.stderr.strip() or "no frames decoded"
            raise VideoTelemetryError(
                f"Video '{video_path.name}' produced no frames: {stderr}"
            )
        extracted = [fallback_path]

    return [(idx / sample_fps, frame_path) for idx, frame_path in enumerate(extracted)]


def _prepare_video_frames(
    upload_dir: Path,
    sample_fps: float,
) -> tuple[list[_SampledFrameSpec], list[str]]:
    video_files = sorted(
        p
        for p in upload_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not video_files:
        raise VideoTelemetryError("No video file found in upload directory.")
    video_path = video_files[0]

    srt_files = sorted(
        p
        for p in upload_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SRT_EXTENSIONS
    )
    srt_path = srt_files[0] if srt_files else None

    telemetry = load_video_telemetry(video_path, srt_path)
    warnings: list[str] = [telemetry.warning] if telemetry.warning else []

    tmp_sample_dir = upload_dir / "_sampled_tmp"
    sampled = sample_video_frames(video_path, tmp_sample_dir, sample_fps)

    specs: list[_SampledFrameSpec] = []
    for timestamp, frame_img_path in sampled:
        pos = interpolate_position(telemetry.samples, timestamp)
        specs.append(
            _SampledFrameSpec(
                source_file=video_path.name,
                timestamp=timestamp,
                longitude=pos.longitude,
                latitude=pos.latitude,
                location_accuracy_m=pos.location_accuracy_m,
                gimbal_pitch_deg=pos.gimbal_pitch_deg,
                oblique=pos.oblique,
                temp_image_path=frame_img_path,
            )
        )

    return specs, warnings


def _prepare_photo_frames(
    upload_dir: Path,
) -> tuple[list[_SampledFrameSpec], list[str]]:
    photo_files = sorted(
        p
        for p in upload_dir.iterdir()
        if p.is_file() and p.suffix.lower() in PHOTO_EXTENSIONS
    )
    if not photo_files:
        raise ValueError("No photo files found in upload directory.")

    metas, warnings = extract_photos_metadata(photo_files)
    by_name = {p.name: p for p in photo_files}

    paired = [(meta, by_name[meta.source_file]) for meta in metas]
    paired.sort(
        key=lambda item: (
            item[0].captured_at is None,
            item[0].timestamp,
            item[0].source_file,
        )
    )

    specs = [
        _SampledFrameSpec(
            source_file=meta.source_file,
            timestamp=meta.timestamp,
            longitude=meta.longitude,
            latitude=meta.latitude,
            location_accuracy_m=meta.location_accuracy_m,
            gimbal_pitch_deg=meta.gimbal_pitch_deg,
            oblique=meta.oblique,
            temp_image_path=photo_path,
        )
        for meta, photo_path in paired
    ]
    return specs, warnings


def process_upload(
    db: Session,
    upload_id: uuid.UUID,
    settings: Settings,
    detector: DetectorProtocol | None = None,
) -> None:
    """Run metadata extraction, frame sampling, YOLO inference, and de-duplication for an upload."""
    upload = db.get(Upload, upload_id)
    if upload is None:
        return

    upload_dir = Path(settings.data_dir) / "uploads" / str(upload_id)
    tmp_sample_dir = upload_dir / "_sampled_tmp"
    active_detector = detector or UltralyticsYoloDetector(
        model_path=Path(settings.model_path),
        min_confidence=settings.min_confidence,
        slice_height=settings.sahi_slice_height,
        slice_width=settings.sahi_slice_width,
        overlap_height_ratio=settings.sahi_overlap_height_ratio,
        overlap_width_ratio=settings.sahi_overlap_width_ratio,
        postprocess_type=settings.sahi_postprocess_type,
        postprocess_match_metric=settings.sahi_postprocess_match_metric,
        postprocess_match_threshold=settings.sahi_postprocess_match_threshold,
    )

    try:
        upload.status = UploadStatus.PROCESSING.value
        upload.progress = 5
        upload.error = None
        db.commit()

        if upload.kind == UploadKind.VIDEO.value:
            frame_specs, warnings = _prepare_video_frames(
                upload_dir, settings.frame_sample_fps
            )
        else:
            frame_specs, warnings = _prepare_photo_frames(upload_dir)

        upload.warnings = warnings
        upload.progress = 20
        db.commit()

        total_frames = max(1, len(frame_specs))
        candidates: list[FrameCandidate] = []
        for idx, spec in enumerate(frame_specs):
            raw_preds = active_detector.predict(spec.temp_image_path)
            validated: list[ValidatedDetection] = validate_predictions(
                raw_preds,
                settings.min_confidence,
                match_threshold=settings.sahi_postprocess_match_threshold,
                match_metric=settings.sahi_postprocess_match_metric,
            )
            candidates.append(
                FrameCandidate(
                    frame_index=idx,
                    longitude=spec.longitude,
                    latitude=spec.latitude,
                    detections=validated,
                )
            )
            upload.progress = 20 + int(((idx + 1) / total_frames) * 65)
            db.commit()

        surviving_by_frame = deduplicate_detections(
            candidates, settings.dedupe_radius_m
        )

        saved_frames_dir = upload_dir / "frames"
        for idx, spec in enumerate(frame_specs):
            frame_id = uuid.uuid4()
            surviving_dets = surviving_by_frame.get(idx, [])
            point = _make_point(spec.longitude, spec.latitude)

            rel_image_path: str | None = None
            if surviving_dets:
                saved_frames_dir.mkdir(parents=True, exist_ok=True)
                dest_img = saved_frames_dir / f"{frame_id}.jpg"
                shutil.copyfile(spec.temp_image_path, dest_img)
                rel_image_path = f"uploads/{upload_id}/frames/{frame_id}.jpg"

            frame_row = Frame(
                id=frame_id,
                upload_id=upload.id,
                source_file=spec.source_file,
                timestamp=spec.timestamp,
                location=point,
                location_accuracy_m=spec.location_accuracy_m,
                gimbal_pitch_deg=spec.gimbal_pitch_deg,
                oblique=spec.oblique,
                image_path=rel_image_path,
            )
            db.add(frame_row)
            db.flush()

            for det in surviving_dets:
                db.add(
                    Detection(
                        frame_id=frame_row.id,
                        class_name=det.class_name.value,
                        confidence=det.confidence,
                        bbox=det.bbox,
                        location=point,
                    )
                )

        upload.progress = 100
        upload.status = UploadStatus.DONE.value
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Upload %s processing failed: %s", upload_id, exc)
        db.rollback()
        failed_upload = db.get(Upload, upload_id)
        if failed_upload is not None:
            failed_upload.status = UploadStatus.FAILED.value
            failed_upload.error = str(exc)
            db.commit()
    finally:
        shutil.rmtree(tmp_sample_dir, ignore_errors=True)


def process_upload_job(
    upload_id: uuid.UUID,
    settings: Settings,
    detector: DetectorProtocol | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> None:
    """Background task entry point (plain def so heavy CPU/IO work never blocks the event loop)."""
    factory = session_factory or get_session_factory(settings.database_url)
    with factory() as db:
        process_upload(db=db, upload_id=upload_id, settings=settings, detector=detector)
