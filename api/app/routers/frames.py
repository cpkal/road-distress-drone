import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.services.uploads import get_frame_image_path

router = APIRouter(prefix="/frames", tags=["frames"])


@router.get("/{frame_id}/image", response_class=FileResponse)
def get_frame_image(
    frame_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    image_path = get_frame_image_path(db=db, frame_id=frame_id, settings=settings)
    if image_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Image for frame '{frame_id}' not found.",
        )
    return FileResponse(path=image_path, media_type="image/jpeg")
