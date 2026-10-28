import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.distress import DistressClass
from app.schemas.detection import DetectionDetail, MapDetectionFeatureCollection
from app.services.detections import get_detection_detail, list_map_detections_geojson

router = APIRouter(prefix="/detections", tags=["detections"])


@router.get("", response_model=MapDetectionFeatureCollection)
def get_detections(
    db: Annotated[Session, Depends(get_db)],
    classes: Annotated[list[DistressClass] | None, Query(alias="class")] = None,
    upload_id: Annotated[uuid.UUID | None, Query()] = None,
) -> MapDetectionFeatureCollection:
    return list_map_detections_geojson(
        db=db,
        classes=classes,
        upload_id=upload_id,
    )


@router.get("/{detection_id}", response_model=DetectionDetail)
def get_detection(
    detection_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> DetectionDetail:
    detail = get_detection_detail(db=db, detection_id=detection_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Detection '{detection_id}' not found.",
        )
    return detail
