import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models.upload import UploadStatus
from app.schemas.detection import DetectionFeatureCollection
from app.schemas.upload import UploadCreateResponse, UploadDetail, UploadSummary
from app.services.detector import DetectorProtocol, get_detector
from app.services.processing import process_upload_job
from app.services.uploads import (
    create_upload,
    get_upload_detail,
    get_upload_detections_geojson,
    list_uploads,
)

router = APIRouter(prefix="/uploads", tags=["uploads"])

JobRunner = Callable[[uuid.UUID, Settings, DetectorProtocol | None], None]


def get_job_runner() -> JobRunner:
    return process_upload_job


@router.post(
    "", response_model=UploadCreateResponse, status_code=status.HTTP_201_CREATED
)
def post_upload(
    files: Annotated[list[UploadFile], File(...)],
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    detector: Annotated[DetectorProtocol, Depends(get_detector)],
    job_runner: Annotated[JobRunner, Depends(get_job_runner)],
) -> UploadCreateResponse:
    upload = create_upload(db=db, files=files, settings=settings)
    background_tasks.add_task(job_runner, upload.id, settings, detector)
    return UploadCreateResponse(id=upload.id, status=UploadStatus(upload.status))


@router.get("", response_model=list[UploadSummary])
def get_uploads(
    db: Annotated[Session, Depends(get_db)],
) -> list[UploadSummary]:
    return list_uploads(db=db)


@router.get("/{upload_id}", response_model=UploadDetail)
def get_upload(
    upload_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> UploadDetail:
    detail = get_upload_detail(db=db, upload_id=upload_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upload '{upload_id}' not found.",
        )
    return detail


@router.get("/{upload_id}/detections", response_model=DetectionFeatureCollection)
def get_upload_detections(
    upload_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> DetectionFeatureCollection:
    collection = get_upload_detections_geojson(db=db, upload_id=upload_id)
    if collection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upload '{upload_id}' not found.",
        )
    return collection
