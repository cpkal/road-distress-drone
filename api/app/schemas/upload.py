import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.upload import UploadKind, UploadStatus


class UploadCreateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: UploadStatus


class UploadSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: UploadKind
    status: UploadStatus
    progress: int = Field(ge=0, le=100)
    frame_count: int = Field(ge=0, default=0)
    detection_count: int = Field(ge=0, default=0)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime


class UploadDetail(UploadSummary):
    error: str | None = None
