from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class UploadKind(StrEnum):
    VIDEO = "video"
    PHOTOS = "photos"


class UploadStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UploadStatus.QUEUED.value, index=True
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    frames: Mapped[list[Frame]] = relationship(
        back_populates="upload", cascade="all, delete-orphan"
    )


class Frame(Base):
    __tablename__ = "frames"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    upload_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_file: Mapped[str] = mapped_column(String(512), nullable=False)
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)
    location: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True), nullable=True
    )
    location_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    gimbal_pitch_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    oblique: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    upload: Mapped[Upload] = relationship(back_populates="frames")
    detections: Mapped[list[Detection]] = relationship(
        back_populates="frame", cascade="all, delete-orphan"
    )


class Detection(Base):
    __tablename__ = "detections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    frame_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("frames.id", ondelete="CASCADE"), nullable=False, index=True
    )
    class_name: Mapped[str] = mapped_column(
        "class", String(64), nullable=False, index=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    bbox: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    location: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True), nullable=True
    )

    frame: Mapped[Frame] = relationship(back_populates="detections")
