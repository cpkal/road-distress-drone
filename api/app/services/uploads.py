import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.distress import DistressClass
from app.models.upload import Detection, Frame, Upload, UploadKind, UploadStatus
from app.schemas.detection import (
    DetectionFeature,
    DetectionFeatureCollection,
    DetectionProperties,
    GeoJSONPoint,
)
from app.schemas.upload import UploadDetail, UploadSummary

VIDEO_EXTENSIONS = {".mp4", ".mov"}
SRT_EXTENSIONS = {".srt"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg"}
ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS | SRT_EXTENSIONS | PHOTO_EXTENSIONS
CHUNK_SIZE_BYTES = 1024 * 1024  # 1 MiB


def recover_interrupted_uploads(db: Session) -> int:
    """Mark any uploads left in 'queued' or 'processing' as 'failed' on server startup."""
    stmt = (
        update(Upload)
        .where(
            Upload.status.in_(
                [UploadStatus.QUEUED.value, UploadStatus.PROCESSING.value]
            )
        )
        .values(
            status=UploadStatus.FAILED.value,
            error="Server restarted before processing completed.",
        )
    )
    result = db.execute(stmt)
    db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def _classify_and_validate_files(
    files: Sequence[UploadFile],
    settings: Settings,
) -> tuple[UploadKind, list[tuple[UploadFile, str]]]:
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one file must be uploaded.",
        )

    videos: list[tuple[UploadFile, str]] = []
    srts: list[tuple[UploadFile, str]] = []
    photos: list[tuple[UploadFile, str]] = []
    seen_names: set[str] = set()

    for upload_file in files:
        raw_name = (upload_file.filename or "").strip()
        safe_name = Path(raw_name).name
        if not safe_name or safe_name in {".", ".."}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is missing a valid filename.",
            )

        ext = Path(safe_name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            allowed_str = ", ".join(sorted(ALLOWED_EXTENSIONS))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported file type '{ext}' for '{safe_name}'. Allowed: {allowed_str}.",
            )

        if safe_name in seen_names:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate filename '{safe_name}' in upload.",
            )
        seen_names.add(safe_name)

        if ext in VIDEO_EXTENSIONS:
            videos.append((upload_file, safe_name))
        elif ext in SRT_EXTENSIONS:
            srts.append((upload_file, safe_name))
        else:
            photos.append((upload_file, safe_name))

    if photos and (videos or srts):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot mix video/SRT files and photos in the same upload.",
        )

    if videos or srts:
        if len(videos) != 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Video upload must include exactly one video file (.mp4 or .mov).",
            )
        if len(srts) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At most one sidecar .SRT file may be uploaded with a video.",
            )
        return UploadKind.VIDEO, [*videos, *srts]

    if len(photos) > settings.max_photos_per_upload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Too many photos ({len(photos)}). "
                f"Maximum allowed per upload is {settings.max_photos_per_upload}."
            ),
        )

    return UploadKind.PHOTOS, photos


def _stream_upload_files(
    upload_dir: Path,
    validated_files: list[tuple[UploadFile, str]],
    max_video_bytes: int,
) -> None:
    upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        for upload_file, safe_name in validated_files:
            dest_path = upload_dir / safe_name
            ext = Path(safe_name).suffix.lower()
            is_video = ext in VIDEO_EXTENSIONS
            total_bytes = 0

            with dest_path.open("wb") as out_file:
                while True:
                    chunk = upload_file.file.read(CHUNK_SIZE_BYTES)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if is_video and total_bytes > max_video_bytes:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=(
                                f"Video file '{safe_name}' exceeds maximum size of "
                                f"{max_video_bytes // (1024 * 1024)} MB."
                            ),
                        )
                    out_file.write(chunk)

            if total_bytes == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Uploaded file '{safe_name}' is empty.",
                )
    except Exception:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise


def create_upload(
    db: Session,
    files: Sequence[UploadFile],
    settings: Settings,
) -> Upload:
    kind, validated_files = _classify_and_validate_files(files, settings)
    upload_id = uuid.uuid4()
    upload_dir = Path(settings.data_dir) / "uploads" / str(upload_id)
    max_video_bytes = settings.max_video_size_mb * 1024 * 1024

    _stream_upload_files(upload_dir, validated_files, max_video_bytes)

    upload = Upload(
        id=upload_id,
        kind=kind.value,
        status=UploadStatus.QUEUED.value,
        progress=0,
        error=None,
        warnings=[],
    )
    try:
        db.add(upload)
        db.commit()
        db.refresh(upload)
    except Exception:
        db.rollback()
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise

    return upload


def _counts_subqueries():
    frame_counts = (
        select(
            Frame.upload_id.label("upload_id"),
            func.count(Frame.id).label("frame_count"),
        )
        .group_by(Frame.upload_id)
        .subquery()
    )
    detection_counts = (
        select(
            Frame.upload_id.label("upload_id"),
            func.count(Detection.id).label("detection_count"),
        )
        .join(Detection, Detection.frame_id == Frame.id)
        .group_by(Frame.upload_id)
        .subquery()
    )
    return frame_counts, detection_counts


def list_uploads(db: Session) -> list[UploadSummary]:
    frame_counts, detection_counts = _counts_subqueries()
    stmt = (
        select(
            Upload,
            func.coalesce(frame_counts.c.frame_count, 0).label("frame_count"),
            func.coalesce(detection_counts.c.detection_count, 0).label(
                "detection_count"
            ),
        )
        .outerjoin(frame_counts, frame_counts.c.upload_id == Upload.id)
        .outerjoin(detection_counts, detection_counts.c.upload_id == Upload.id)
        .order_by(Upload.created_at.desc(), Upload.id.desc())
    )
    rows = db.execute(stmt).all()
    return [
        UploadSummary(
            id=upload.id,
            kind=UploadKind(upload.kind),
            status=UploadStatus(upload.status),
            progress=upload.progress,
            frame_count=int(frame_count),
            detection_count=int(detection_count),
            warnings=list(upload.warnings or []),
            created_at=upload.created_at,
        )
        for upload, frame_count, detection_count in rows
    ]


def get_upload_detail(db: Session, upload_id: uuid.UUID) -> UploadDetail | None:
    frame_counts, detection_counts = _counts_subqueries()
    stmt = (
        select(
            Upload,
            func.coalesce(frame_counts.c.frame_count, 0).label("frame_count"),
            func.coalesce(detection_counts.c.detection_count, 0).label(
                "detection_count"
            ),
        )
        .outerjoin(frame_counts, frame_counts.c.upload_id == Upload.id)
        .outerjoin(detection_counts, detection_counts.c.upload_id == Upload.id)
        .where(Upload.id == upload_id)
    )
    row = db.execute(stmt).first()
    if row is None:
        return None
    upload, frame_count, detection_count = row
    return UploadDetail(
        id=upload.id,
        kind=UploadKind(upload.kind),
        status=UploadStatus(upload.status),
        progress=upload.progress,
        frame_count=int(frame_count),
        detection_count=int(detection_count),
        warnings=list(upload.warnings or []),
        error=upload.error,
        created_at=upload.created_at,
    )


def get_upload_detections_geojson(
    db: Session,
    upload_id: uuid.UUID,
) -> DetectionFeatureCollection | None:
    upload_exists = db.scalar(select(Upload.id).where(Upload.id == upload_id))
    if upload_exists is None:
        return None

    stmt = (
        select(
            Detection,
            Frame,
            func.ST_X(Detection.location).label("lon"),
            func.ST_Y(Detection.location).label("lat"),
        )
        .join(Frame, Detection.frame_id == Frame.id)
        .where(Frame.upload_id == upload_id)
        .order_by(Frame.timestamp.asc(), Detection.id.asc())
    )
    rows = db.execute(stmt).all()

    features: list[DetectionFeature] = []
    for det, frame, lon, lat in rows:
        geometry = (
            GeoJSONPoint(coordinates=(float(lon), float(lat)))
            if lon is not None and lat is not None
            else None
        )
        props = DetectionProperties(
            id=det.id,
            class_name=DistressClass(det.class_name),
            confidence=det.confidence,
            bbox=[float(v) for v in det.bbox],
            frame_id=frame.id,
            image_url=f"/frames/{frame.id}/image",
            timestamp=frame.timestamp,
            location_accuracy_m=frame.location_accuracy_m,
            oblique=frame.oblique,
        )
        features.append(DetectionFeature(geometry=geometry, properties=props))

    return DetectionFeatureCollection(features=features)


def get_frame_image_path(
    db: Session,
    frame_id: uuid.UUID,
    settings: Settings,
) -> Path | None:
    frame = db.get(Frame, frame_id)
    if frame is None or not frame.image_path:
        return None

    data_root = Path(settings.data_dir).resolve()
    candidate = (data_root / frame.image_path).resolve()
    if not candidate.is_relative_to(data_root) or not candidate.is_file():
        return None
    return candidate
