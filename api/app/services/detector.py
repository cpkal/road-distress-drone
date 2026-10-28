import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import Depends

from app.config import Settings, get_settings
from app.domain.distress import DistressClass, normalize_distress_class

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
    """CPU YOLO detector backed by Ultralytics."""

    def __init__(self, model_path: Path, min_confidence: float = 0.25) -> None:
        self._model_path = model_path
        self._min_confidence = min_confidence
        self._model: Any = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        if not self._model_path.is_file():
            raise DetectionError(
                f"YOLO model weights not found at '{self._model_path}'."
            )
        try:
            from ultralytics import YOLO

            self._model = YOLO(str(self._model_path))
        except Exception as exc:
            raise DetectionError(
                f"Failed to load YOLO model from '{self._model_path}': {exc}"
            ) from exc
        return self._model

    def predict(self, image_path: Path) -> Sequence[RawPrediction]:
        if not image_path.is_file():
            raise DetectionError(f"Frame image not found at '{image_path}'.")
        model = self._load_model()
        try:
            results = model.predict(
                source=str(image_path),
                conf=self._min_confidence,
                device="cpu",
                verbose=False,
            )
        except Exception as exc:
            raise DetectionError(
                f"YOLO inference failed on '{image_path.name}': {exc}"
            ) from exc

        predictions: list[RawPrediction] = []
        for result in results:
            names: dict[int, str] = getattr(result, "names", {}) or {}
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            xyxyn_list = boxes.xyxyn.tolist()
            conf_list = boxes.conf.tolist()
            cls_list = boxes.cls.tolist()
            for coords, conf, cls_idx in zip(
                xyxyn_list, conf_list, cls_list, strict=False
            ):
                raw_label = names.get(int(cls_idx), str(int(cls_idx)))
                predictions.append(
                    RawPrediction(
                        class_name=str(raw_label),
                        confidence=float(conf),
                        bbox=[float(c) for c in coords[:4]],
                    )
                )
        return predictions


def _clamp_01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def validate_predictions(
    raw_predictions: Sequence[RawPrediction],
    min_confidence: float,
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
    return validated


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
    )
