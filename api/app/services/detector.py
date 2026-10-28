import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import Depends

from app.config import Settings, get_settings
from app.domain.distress import (
    CRACK_DISTRESS_CLASSES,
    DistressClass,
    normalize_distress_class,
)

EARTH_RADIUS_M = 6_371_000.0


class DetectionError(RuntimeError):
    """Raised when YOLO model loading or inference fails."""


@dataclass(frozen=True, slots=True)
class RawPrediction:
    class_name: str
    confidence: float
    bbox: list[float]


@dataclass(frozen=True, slots=True)
class ValidatedDetection:
    class_name: DistressClass
    confidence: float
    bbox: list[float]


@dataclass(frozen=True, slots=True)
class FrameCandidate:
    frame_index: int
    longitude: float | None
    latitude: float | None
    detections: Sequence[ValidatedDetection]


class DetectorProtocol(Protocol):
    def predict(self, image_path: Path) -> Sequence[RawPrediction]: ...


class UltralyticsYoloDetector:
    """CPU YOLO detector backed by Ultralytics and SAHI sliced inference."""

    def __init__(
        self,
        model_path: Path,
        min_confidence: float = 0.25,
        slice_height: int = 640,
        slice_width: int = 640,
        overlap_height_ratio: float = 0.2,
        overlap_width_ratio: float = 0.2,
        postprocess_type: str = "GREEDYNMM",
        postprocess_match_metric: str = "IOS",
        postprocess_match_threshold: float = 0.35,
    ) -> None:
        self._model_path = model_path
        self._min_confidence = min_confidence
        self._slice_height = slice_height
        self._slice_width = slice_width
        self._overlap_height_ratio = overlap_height_ratio
        self._overlap_width_ratio = overlap_width_ratio
        self._postprocess_type = postprocess_type
        self._postprocess_match_metric = postprocess_match_metric
        self._postprocess_match_threshold = postprocess_match_threshold
        self._model: Any = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        if not self._model_path.is_file():
            raise DetectionError(
                f"YOLO model weights not found at '{self._model_path}'."
            )
        try:
            from sahi import AutoDetectionModel

            self._model = AutoDetectionModel.from_pretrained(
                model_type="ultralytics",
                model_path=str(self._model_path),
                confidence_threshold=self._min_confidence,
                device="cpu",
            )
        except Exception as exc:
            raise DetectionError(
                f"Failed to load YOLO model from '{self._model_path}': {exc}"
            ) from exc
        return self._model

    def predict(self, image_path: Path) -> Sequence[RawPrediction]:
        if not image_path.is_file():
            raise DetectionError(f"Frame image not found at '{image_path}'.")
        detection_model = self._load_model()
        try:
            from sahi.predict import get_sliced_prediction

            result = get_sliced_prediction(
                image=str(image_path),
                detection_model=detection_model,
                slice_height=self._slice_height,
                slice_width=self._slice_width,
                overlap_height_ratio=self._overlap_height_ratio,
                overlap_width_ratio=self._overlap_width_ratio,
                perform_standard_pred=False,
                postprocess_type=self._postprocess_type,
                postprocess_match_metric=self._postprocess_match_metric,
                postprocess_match_threshold=self._postprocess_match_threshold,
                verbose=0,
            )
        except Exception as exc:
            raise DetectionError(
                f"YOLO SAHI inference failed on '{image_path.name}': {exc}"
            ) from exc

        img_w = float(getattr(result, "image_width", 0) or 0)
        img_h = float(getattr(result, "image_height", 0) or 0)
        if img_w <= 0 or img_h <= 0:
            raise DetectionError(
                f"Invalid image dimensions ({img_w}x{img_h}) returned for '{image_path.name}'."
            )

        predictions: list[RawPrediction] = []
        for obj_pred in getattr(result, "object_prediction_list", []) or []:
            x_min, y_min, x_max, y_max = obj_pred.bbox.to_xyxy()
            predictions.append(
                RawPrediction(
                    class_name=str(obj_pred.category.name),
                    confidence=float(obj_pred.score.value),
                    bbox=[
                        _clamp_01(float(x_min) / img_w),
                        _clamp_01(float(y_min) / img_h),
                        _clamp_01(float(x_max) / img_w),
                        _clamp_01(float(y_max) / img_h),
                    ],
                )
            )
        return predictions


def _clamp_01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _box_match_score(
    box_a: Sequence[float],
    box_b: Sequence[float],
    match_metric: str = "IOS",
) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter_area = inter_w * inter_h
    if inter_area <= 0.0:
        return 0.0

    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)

    if match_metric.upper() == "IOU":
        denom = area_a + area_b - inter_area
    else:
        denom = min(area_a, area_b)

    if denom <= 0.0:
        return 0.0
    return inter_area / denom


def merge_overlapping_crack_detections(
    detections: Sequence[ValidatedDetection],
    match_threshold: float = 0.5,
    match_metric: str = "IOS",
) -> list[ValidatedDetection]:
    """Merge overlapping same-class cracking detections within a frame using Greedy NMM."""
    non_crack: list[ValidatedDetection] = []
    crack_by_class: dict[DistressClass, list[ValidatedDetection]] = {}

    for det in detections:
        if det.class_name in CRACK_DISTRESS_CLASSES:
            crack_by_class.setdefault(det.class_name, []).append(det)
        else:
            non_crack.append(det)

    merged_cracks: list[ValidatedDetection] = []
    for cls_name, cls_dets in crack_by_class.items():
        remaining = sorted(cls_dets, key=lambda d: d.confidence, reverse=True)
        while remaining:
            seed = remaining.pop(0)
            curr_bbox = list(seed.bbox)
            best_conf = seed.confidence

            changed = True
            while changed and remaining:
                changed = False
                still_unmerged: list[ValidatedDetection] = []
                for other in remaining:
                    score = _box_match_score(curr_bbox, other.bbox, match_metric)
                    if score >= match_threshold:
                        curr_bbox = [
                            min(curr_bbox[0], other.bbox[0]),
                            min(curr_bbox[1], other.bbox[1]),
                            max(curr_bbox[2], other.bbox[2]),
                            max(curr_bbox[3], other.bbox[3]),
                        ]
                        best_conf = max(best_conf, other.confidence)
                        changed = True
                    else:
                        still_unmerged.append(other)
                remaining = still_unmerged

            merged_cracks.append(
                ValidatedDetection(
                    class_name=cls_name,
                    confidence=best_conf,
                    bbox=curr_bbox,
                )
            )

    return [*merged_cracks, *non_crack]


def validate_predictions(
    raw_predictions: Sequence[RawPrediction],
    min_confidence: float,
    match_threshold: float = 0.5,
    match_metric: str = "IOS",
) -> list[ValidatedDetection]:
    validated: list[ValidatedDetection] = []
    for pred in raw_predictions:
        canonical_class = normalize_distress_class(pred.class_name)
        conf = float(pred.confidence)
        if conf < min_confidence:
            continue
        if len(pred.bbox) != 4:
            raise DetectionError(
                f"Expected 4 bounding box coordinates [x_min, y_min, x_max, y_max], got {len(pred.bbox)}."
            )
        x_min, y_min, x_max, y_max = (_clamp_01(v) for v in pred.bbox)
        if x_max < x_min:
            x_min, x_max = x_max, x_min
        if y_max < y_min:
            y_min, y_max = y_max, y_min
        validated.append(
            ValidatedDetection(
                class_name=canonical_class,
                confidence=conf,
                bbox=[x_min, y_min, x_max, y_max],
            )
        )
    return merge_overlapping_crack_detections(
        validated,
        match_threshold=match_threshold,
        match_metric=match_metric,
    )


def haversine_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


@dataclass(slots=True)
class _ActiveTrack:
    class_name: DistressClass
    last_frame_index: int
    last_lon: float
    last_lat: float
    best_frame_index: int
    best_detection: ValidatedDetection


def deduplicate_detections(
    frames: Sequence[FrameCandidate],
    dedupe_radius_m: float,
) -> dict[int, list[ValidatedDetection]]:
    """De-duplicate same-class detections across consecutive frames within dedupe_radius_m.

    - Detections in frames with missing location (longitude/latitude is None) are never merged.
    - Detections within the same frame are never merged with each other.
    - Transitive chains across consecutive frames keep the single highest-confidence detection.
    """
    surviving: dict[int, list[ValidatedDetection]] = {f.frame_index: [] for f in frames}
    active_tracks: list[_ActiveTrack] = []

    def _flush_tracks(keep_from_frame: int | None = None) -> None:
        nonlocal active_tracks
        still_active: list[_ActiveTrack] = []
        for track in active_tracks:
            if (
                keep_from_frame is not None
                and track.last_frame_index == keep_from_frame
            ):
                still_active.append(track)
            else:
                surviving.setdefault(track.best_frame_index, []).append(
                    track.best_detection
                )
        active_tracks = still_active

    prev_frame_index: int | None = None
    for frame in frames:
        if frame.longitude is None or frame.latitude is None:
            _flush_tracks(keep_from_frame=None)
            surviving[frame.frame_index].extend(frame.detections)
            prev_frame_index = None
            continue

        lon = frame.longitude
        lat = frame.latitude

        if prev_frame_index is None:
            _flush_tracks(keep_from_frame=None)

        # Group current frame's detections by class, sorted highest confidence first
        by_class: dict[DistressClass, list[ValidatedDetection]] = {}
        for det in frame.detections:
            by_class.setdefault(det.class_name, []).append(det)
        for det_list in by_class.values():
            det_list.sort(key=lambda d: d.confidence, reverse=True)

        matched_tracks: set[int] = set()
        new_tracks: list[_ActiveTrack] = []

        for cls_name, det_list in by_class.items():
            # Candidate tracks of the same class from the immediately preceding geolocated frame
            candidate_indices = [
                idx
                for idx, tr in enumerate(active_tracks)
                if tr.class_name == cls_name
                and tr.last_frame_index == prev_frame_index
                and idx not in matched_tracks
                and haversine_distance_m(tr.last_lon, tr.last_lat, lon, lat)
                <= dedupe_radius_m
            ]
            # Sort candidate tracks by distance then highest confidence
            candidate_indices.sort(
                key=lambda idx: (
                    haversine_distance_m(
                        active_tracks[idx].last_lon,
                        active_tracks[idx].last_lat,
                        lon,
                        lat,
                    ),
                    -active_tracks[idx].best_detection.confidence,
                )
            )

            for det in det_list:
                if candidate_indices:
                    track_idx = candidate_indices.pop(0)
                    matched_tracks.add(track_idx)
                    track = active_tracks[track_idx]
                    track.last_frame_index = frame.frame_index
                    track.last_lon = lon
                    track.last_lat = lat
                    if det.confidence > track.best_detection.confidence:
                        track.best_frame_index = frame.frame_index
                        track.best_detection = det
                else:
                    new_tracks.append(
                        _ActiveTrack(
                            class_name=cls_name,
                            last_frame_index=frame.frame_index,
                            last_lon=lon,
                            last_lat=lat,
                            best_frame_index=frame.frame_index,
                            best_detection=det,
                        )
                    )

        # Any track not extended onto `frame.frame_index` is finished and flushed
        _flush_tracks(keep_from_frame=frame.frame_index)
        active_tracks.extend(new_tracks)
        prev_frame_index = frame.frame_index

    _flush_tracks(keep_from_frame=None)
    return surviving


def get_detector(
    settings: Annotated[Settings, Depends(get_settings)],
) -> DetectorProtocol:
    return UltralyticsYoloDetector(
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
