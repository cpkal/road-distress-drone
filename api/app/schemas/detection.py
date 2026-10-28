import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.distress import DistressClass
from app.models.upload import UploadKind


class GeoJSONPoint(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]


class DetectionProperties(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: uuid.UUID
    class_name: DistressClass = Field(alias="class", serialization_alias="class")
    confidence: float
    bbox: list[float]
    frame_id: uuid.UUID
    image_url: str
    timestamp: float
    location_accuracy_m: float | None = None
    oblique: bool


class DetectionFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: GeoJSONPoint | None
    properties: DetectionProperties


class DetectionFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[DetectionFeature]


class MapDetectionProperties(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: uuid.UUID
    class_name: DistressClass = Field(alias="class", serialization_alias="class")
    confidence: float
    upload_id: uuid.UUID


class MapDetectionFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: GeoJSONPoint
    properties: MapDetectionProperties


class MapDetectionFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[MapDetectionFeature]
    truncated: bool = False


class FrameDetectionItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: uuid.UUID
    class_name: DistressClass = Field(alias="class", serialization_alias="class")
    confidence: float
    bbox: list[float]


class DetectionDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: uuid.UUID
    class_name: DistressClass = Field(alias="class", serialization_alias="class")
    confidence: float
    bbox: list[float]
    location: GeoJSONPoint | None
    location_accuracy_m: float | None = None
    oblique: bool
    timestamp: float
    source_file: str
    upload_id: uuid.UUID
    upload_kind: UploadKind
    upload_created_at: datetime
    frame_id: uuid.UUID
    image_url: str
    frame_detections: list[FrameDetectionItem] = Field(default_factory=list)
