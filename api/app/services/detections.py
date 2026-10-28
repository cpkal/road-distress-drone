import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.distress import DistressClass
from app.models.upload import Detection, Frame, Upload, UploadKind
from app.schemas.detection import (
    DetectionDetail,
    GeoJSONPoint,
    MapDetectionFeature,
    MapDetectionFeatureCollection,
    MapDetectionProperties,
)

MAX_MAP_DETECTIONS = 10_000


def list_map_detections_geojson(
    db: Session,
    classes: Sequence[DistressClass] | None = None,
    upload_id: uuid.UUID | None = None,
    limit: int = MAX_MAP_DETECTIONS,
) -> MapDetectionFeatureCollection:
    stmt = (
        select(
            Detection,
            Frame.upload_id.label("upload_id"),
            func.ST_X(Detection.location).label("lon"),
            func.ST_Y(Detection.location).label("lat"),
        )
        .join(Frame, Detection.frame_id == Frame.id)
        .where(Detection.location.is_not(None))
    )

    if classes:
        stmt = stmt.where(Detection.class_name.in_([c.value for c in classes]))

    if upload_id is not None:
        stmt = stmt.where(Frame.upload_id == upload_id)

    stmt = stmt.order_by(Frame.timestamp.asc(), Detection.id.asc()).limit(limit + 1)
    rows = db.execute(stmt).all()

    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]

    features = [
        MapDetectionFeature(
            geometry=GeoJSONPoint(coordinates=(float(lon), float(lat))),
            properties=MapDetectionProperties(
                id=det.id,
                class_name=DistressClass(det.class_name),
                confidence=det.confidence,
                upload_id=frame_upload_id,
            ),
        )
        for det, frame_upload_id, lon, lat in rows
    ]

    return MapDetectionFeatureCollection(features=features, truncated=truncated)


def get_detection_detail(
    db: Session,
    detection_id: uuid.UUID,
) -> DetectionDetail | None:
    stmt = (
        select(
            Detection,
            Frame,
            Upload,
            func.ST_X(Detection.location).label("lon"),
            func.ST_Y(Detection.location).label("lat"),
        )
        .join(Frame, Detection.frame_id == Frame.id)
        .join(Upload, Frame.upload_id == Upload.id)
        .where(Detection.id == detection_id)
    )
    row = db.execute(stmt).first()
    if row is None:
        return None

    det, frame, upload, lon, lat = row
    location = (
        GeoJSONPoint(coordinates=(float(lon), float(lat)))
        if lon is not None and lat is not None
        else None
    )

    return DetectionDetail(
        id=det.id,
        class_name=DistressClass(det.class_name),
        confidence=det.confidence,
        bbox=[float(v) for v in det.bbox],
        location=location,
        location_accuracy_m=frame.location_accuracy_m,
        oblique=frame.oblique,
        timestamp=frame.timestamp,
        source_file=frame.source_file,
        upload_id=upload.id,
        upload_kind=UploadKind(upload.kind),
        upload_created_at=upload.created_at,
        frame_id=frame.id,
        image_url=f"/frames/{frame.id}/image",
    )
