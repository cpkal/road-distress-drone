from collections.abc import Callable, Generator, Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db, get_session_factory
from app.domain.distress import (
    DistressClass,
    UnknownDistressClassError,
    normalize_distress_class,
)
from app.main import app
from app.models.upload import Detection, Frame, Upload
from app.services.detector import (
    DetectorProtocol,
    FrameCandidate,
    RawPrediction,
    UltralyticsYoloDetector,
    ValidatedDetection,
    deduplicate_detections,
    get_detector,
    validate_predictions,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_VIDEO = FIXTURES_DIR / "sample_video.mp4"
SAMPLE_SRT = FIXTURES_DIR / "sample_video.srt"
PHOTO_WITH_GPS = FIXTURES_DIR / "photo_with_gps.jpg"
PHOTO_WITHOUT_GPS = FIXTURES_DIR / "photo_without_gps.jpg"


class FakeDetector(DetectorProtocol):
    def __init__(
        self,
        fn: Callable[[int, Path], Sequence[RawPrediction]],
    ) -> None:
        self._fn = fn
        self.call_count = 0

    def predict(self, image_path: Path) -> Sequence[RawPrediction]:
        idx = self.call_count
        self.call_count += 1
        return self._fn(idx, image_path)


@pytest.fixture
def db_session() -> Generator[Session]:
    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(delete(Detection))
        session.execute(delete(Frame))
        session.execute(delete(Upload))
        session.commit()
        yield session
        session.execute(delete(Detection))
        session.execute(delete(Frame))
        session.execute(delete(Upload))
        session.commit()


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    base = get_settings()
    return base.model_copy(
        update={
            "data_dir": str(tmp_path / "data"),
            "models_dir": str(tmp_path / "models"),
            "model_path": str(tmp_path / "models" / "best.pt"),
            "frame_sample_fps": 1.0,
            "min_confidence": 0.25,
            "dedupe_radius_m": 5.0,
        }
    )


def test_normalize_distress_class_accepts_known_and_rejects_unknown() -> None:
    assert (
        normalize_distress_class("transverse cracking")
        == DistressClass.TRANSVERSE_CRACKING
    )
    assert (
        normalize_distress_class("Transverse_Cracking")
        == DistressClass.TRANSVERSE_CRACKING
    )
    assert (
        normalize_distress_class("Transverse crack")
        == DistressClass.TRANSVERSE_CRACKING
    )
    assert (
        normalize_distress_class("Oblique crack") == DistressClass.TRANSVERSE_CRACKING
    )
    assert (
        normalize_distress_class("longitudinal cracking")
        == DistressClass.LONGITUDINAL_CRACKING
    )
    assert (
        normalize_distress_class("Longitudinal crack")
        == DistressClass.LONGITUDINAL_CRACKING
    )
    assert (
        normalize_distress_class("alligator-cracking")
        == DistressClass.ALLIGATOR_CRACKING
    )
    assert (
        normalize_distress_class("Alligator crack") == DistressClass.ALLIGATOR_CRACKING
    )
    assert normalize_distress_class("Block crack") == DistressClass.ALLIGATOR_CRACKING
    assert normalize_distress_class("patch") == DistressClass.PATCH
    assert normalize_distress_class("Repair") == DistressClass.PATCH
    assert normalize_distress_class("POTHOLE") == DistressClass.POTHOLE

    with pytest.raises(UnknownDistressClassError, match="Unknown model distress class"):
        normalize_distress_class("manhole_cover")


def test_validate_predictions_merges_overlapping_crackings_and_preserves_distinct() -> (
    None
):
    raw = [
        # Two overlapping transverse crack detections (one from Transverse crack, one from Oblique crack)
        RawPrediction("Transverse crack", 0.72, [0.10, 0.20, 0.40, 0.50]),
        RawPrediction("Oblique crack", 0.88, [0.25, 0.20, 0.55, 0.50]),
        # A far-away longitudinal crack that does not overlap
        RawPrediction("Longitudinal crack", 0.65, [0.70, 0.10, 0.85, 0.60]),
        # Two overlapping potholes in the same frame should NOT be union-merged by crack postprocessing
        RawPrediction("Pothole", 0.80, [0.10, 0.70, 0.25, 0.85]),
        RawPrediction("Pothole", 0.60, [0.12, 0.72, 0.22, 0.82]),
    ]

    validated = validate_predictions(raw, min_confidence=0.25, match_threshold=0.5)

    transverse = [
        d for d in validated if d.class_name == DistressClass.TRANSVERSE_CRACKING
    ]
    longitudinal = [
        d for d in validated if d.class_name == DistressClass.LONGITUDINAL_CRACKING
    ]
    potholes = [d for d in validated if d.class_name == DistressClass.POTHOLE]

    assert len(transverse) == 1
    assert transverse[0].confidence == pytest.approx(0.88)
    assert transverse[0].bbox == pytest.approx([0.10, 0.20, 0.55, 0.50])
    assert len(longitudinal) == 1
    assert len(potholes) == 2


def test_ultralytics_yolo_detector_uses_sahi_sliced_prediction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    weights_path = tmp_path / "best.pt"
    weights_path.write_bytes(b"fake-weights")
    image_path = tmp_path / "frame.jpg"
    image_path.write_bytes(b"fake-jpeg")

    captured_model_kwargs: dict[str, object] = {}
    captured_slice_kwargs: dict[str, object] = {}
    sentinel_model = object()

    def fake_from_pretrained(**kwargs: object) -> object:
        captured_model_kwargs.update(kwargs)
        return sentinel_model

    def fake_get_sliced_prediction(**kwargs: object) -> object:
        captured_slice_kwargs.update(kwargs)
        fake_pred = SimpleNamespace(
            bbox=SimpleNamespace(to_xyxy=lambda: [200.0, 100.0, 600.0, 500.0]),
            score=SimpleNamespace(value=0.85),
            category=SimpleNamespace(name="Alligator crack"),
        )
        return SimpleNamespace(
            image_width=1000,
            image_height=1000,
            object_prediction_list=[fake_pred],
        )

    import sahi
    import sahi.predict

    monkeypatch.setattr(
        sahi.AutoDetectionModel, "from_pretrained", fake_from_pretrained
    )
    monkeypatch.setattr(
        sahi.predict, "get_sliced_prediction", fake_get_sliced_prediction
    )

    detector = UltralyticsYoloDetector(
        model_path=weights_path,
        min_confidence=0.30,
    )
    preds = detector.predict(image_path)

    assert captured_model_kwargs == {
        "model_type": "ultralytics",
        "model_path": str(weights_path),
        "confidence_threshold": 0.30,
        "device": "cpu",
    }
    assert captured_slice_kwargs == {
        "image": str(image_path),
        "detection_model": sentinel_model,
        "slice_height": 640,
        "slice_width": 640,
        "overlap_height_ratio": 0.2,
        "overlap_width_ratio": 0.2,
        "perform_standard_pred": False,
        "postprocess_type": "GREEDYNMM",
        "postprocess_match_metric": "IOS",
        "postprocess_match_threshold": 0.5,
        "verbose": 0,
    }
    assert len(preds) == 1
    assert preds[0].class_name == "Alligator crack"
    assert preds[0].confidence == pytest.approx(0.85)
    assert preds[0].bbox == pytest.approx([0.2, 0.1, 0.6, 0.5])


def test_deduplicate_detections_transitive_and_same_frame_rules() -> None:
    # ~2 meters apart along longitude at lat 13.7563 (0.000018 deg ~ 1.95 m)
    lon0, lat0 = 100.501700, 13.756300
    lon1, lat1 = 100.501718, 13.756300
    lon2, lat2 = 100.501736, 13.756300
    # ~25 meters away
    lon3, lat3 = 100.501950, 13.756300

    pothole_low = ValidatedDetection(DistressClass.POTHOLE, 0.55, [0.1, 0.1, 0.2, 0.2])
    pothole_Second_same_frame = ValidatedDetection(
        DistressClass.POTHOLE, 0.45, [0.6, 0.6, 0.8, 0.8]
    )
    pothole_high = ValidatedDetection(
        DistressClass.POTHOLE, 0.92, [0.12, 0.12, 0.22, 0.22]
    )
    pothole_mid = ValidatedDetection(
        DistressClass.POTHOLE, 0.70, [0.11, 0.11, 0.21, 0.21]
    )
    patch_det = ValidatedDetection(DistressClass.PATCH, 0.80, [0.3, 0.3, 0.5, 0.5])
    no_gps_det = ValidatedDetection(DistressClass.POTHOLE, 0.60, [0.2, 0.2, 0.4, 0.4])

    frames = [
        # Frame 0 has TWO distinct potholes in the same frame; only one should match Frame 1's single pothole
        FrameCandidate(0, lon0, lat0, [pothole_low, pothole_Second_same_frame]),
        # Frame 1 (2m away) has the same pothole with higher confidence (0.92) + a patch
        FrameCandidate(1, lon1, lat1, [pothole_high, patch_det]),
        # Frame 2 (another 2m away) continues the pothole chain with conf 0.70
        FrameCandidate(2, lon2, lat2, [pothole_mid]),
        # Frame 3 is 25m away (> 5m radius), so its pothole is a separate defect
        FrameCandidate(3, lon3, lat3, [pothole_low]),
        # Frame 4 has no GPS (None), so it is never de-duplicated
        FrameCandidate(4, None, None, [no_gps_det, pothole_mid]),
    ]

    surviving = deduplicate_detections(frames, dedupe_radius_m=5.0)

    # Frame 0 keeps the second same-frame pothole (0.45), while the 0.55->0.92->0.70 chain wins in Frame 1
    assert surviving[0] == [pothole_Second_same_frame]
    assert set(map(id, surviving[1])) == {id(pothole_high), id(patch_det)}
    assert surviving[2] == []
    assert surviving[3] == [pothole_low]
    assert surviving[4] == [no_gps_det, pothole_mid]


def test_video_upload_end_to_end_processing_and_deduplication(
    db_session: Session,
    test_settings: Settings,
) -> None:
    # SRT with 3 samples 2 meters apart so frame 0 and frame 1 pothole merges into frame 1
    close_srt = (
        "1\n00:00:00,000 --> 00:00:01,000\n"
        "[latitude: 13.756300] [longitude: 100.501700] [gimbal_pitch: -89.0]\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n"
        "[latitude: 13.756300] [longitude: 100.501718] [gimbal_pitch: -89.0]\n\n"
        "3\n00:00:02,000 --> 00:00:03,000\n"
        "[latitude: 13.756300] [longitude: 100.501736] [gimbal_pitch: -89.0]\n"
    )

    def fake_predict(idx: int, _path: Path) -> Sequence[RawPrediction]:
        if idx == 0:
            return [RawPrediction("pothole", 0.60, [0.1, 0.2, 0.3, 0.4])]
        if idx == 1:
            return [
                RawPrediction("pothole", 0.91, [0.15, 0.25, 0.35, 0.45]),
                RawPrediction("transverse cracking", 0.77, [0.4, 0.5, 0.8, 0.6]),
            ]
        return []

    detector = FakeDetector(fake_predict)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_detector] = lambda: detector

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/uploads",
                files=[
                    (
                        "files",
                        ("sample_video.mp4", SAMPLE_VIDEO.read_bytes(), "video/mp4"),
                    ),
                    (
                        "files",
                        ("sample_video.srt", close_srt.encode("utf-8"), "text/plain"),
                    ),
                ],
            )
            assert resp.status_code == 201
            upload_id = resp.json()["id"]

            detail_resp = client.get(f"/uploads/{upload_id}")
            assert detail_resp.status_code == 200
            detail = detail_resp.json()
            assert detail["status"] == "done"
            assert detail["progress"] == 100
            assert detail["frame_count"] == 3
            # Pothole in frame 0 (0.60) and frame 1 (0.91) merged into 1 pothole + 1 transverse_cracking in frame 1 = 2 detections
            assert detail["detection_count"] == 2
            assert detail["warnings"] == []
            assert detail["error"] is None
    finally:
        app.dependency_overrides.clear()

    frames = list(
        db_session.scalars(
            select(Frame)
            .where(Frame.upload_id == upload_id)
            .order_by(Frame.timestamp.asc())
        )
    )
    assert len(frames) == 3
    # Frame 0 had its only detection merged into Frame 1, and Frame 2 had 0 detections -> only Frame 1 saves an image
    assert frames[0].image_path is None
    assert frames[1].image_path is not None
    assert (Path(test_settings.data_dir) / frames[1].image_path).is_file()
    assert frames[2].image_path is None

    # Verify PostGIS [lon, lat] order on saved detection
    dets = list(
        db_session.scalars(select(Detection).where(Detection.frame_id == frames[1].id))
    )
    assert len(dets) == 2
    classes = {d.class_name: d for d in dets}
    assert set(classes.keys()) == {"pothole", "transverse_cracking"}
    assert classes["pothole"].confidence == pytest.approx(0.91)
    assert classes["pothole"].bbox == pytest.approx([0.15, 0.25, 0.35, 0.45])
    lon, lat = db_session.execute(
        select(func.ST_X(Detection.location), func.ST_Y(Detection.location)).where(
            Detection.id == classes["pothole"].id
        )
    ).one()
    assert lon == pytest.approx(100.501718, abs=1e-6)
    assert lat == pytest.approx(13.756300, abs=1e-6)


def test_photos_upload_end_to_end_with_and_without_gps(
    db_session: Session,
    test_settings: Settings,
) -> None:
    detector = FakeDetector(
        lambda _idx, _path: [
            RawPrediction("alligator cracking", 0.84, [0.2, 0.2, 0.7, 0.7])
        ]
    )
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_detector] = lambda: detector

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/uploads",
                files=[
                    (
                        "files",
                        (
                            "photo_with_gps.jpg",
                            PHOTO_WITH_GPS.read_bytes(),
                            "image/jpeg",
                        ),
                    ),
                    (
                        "files",
                        (
                            "photo_without_gps.jpg",
                            PHOTO_WITHOUT_GPS.read_bytes(),
                            "image/jpeg",
                        ),
                    ),
                ],
            )
            assert resp.status_code == 201
            upload_id = resp.json()["id"]

            detail = client.get(f"/uploads/{upload_id}").json()
            assert detail["status"] == "done"
            assert detail["progress"] == 100
            assert detail["frame_count"] == 2
            assert detail["detection_count"] == 2
            assert len(detail["warnings"]) == 1
            assert "photo_without_gps.jpg" in detail["warnings"][0]
    finally:
        app.dependency_overrides.clear()

    frames = {
        f.source_file: f
        for f in db_session.scalars(select(Frame).where(Frame.upload_id == upload_id))
    }
    assert frames["photo_with_gps.jpg"].location is not None
    lon, lat = db_session.execute(
        select(func.ST_X(Frame.location), func.ST_Y(Frame.location)).where(
            Frame.id == frames["photo_with_gps.jpg"].id
        )
    ).one()
    assert lon == pytest.approx(100.501762, abs=1e-6)
    assert lat == pytest.approx(13.756331, abs=1e-6)
    assert frames["photo_without_gps.jpg"].location is None


def test_unknown_model_class_marks_upload_failed(
    db_session: Session,
    test_settings: Settings,
) -> None:
    detector = FakeDetector(
        lambda _idx, _path: [
            RawPrediction("invented_class", 0.90, [0.1, 0.1, 0.5, 0.5])
        ]
    )
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_detector] = lambda: detector

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/uploads",
                files=[
                    (
                        "files",
                        (
                            "photo_with_gps.jpg",
                            PHOTO_WITH_GPS.read_bytes(),
                            "image/jpeg",
                        ),
                    )
                ],
            )
            assert resp.status_code == 201
            upload_id = resp.json()["id"]

            detail = client.get(f"/uploads/{upload_id}").json()
            assert detail["status"] == "failed"
            assert detail["error"] is not None
            assert "Unknown model distress class" in detail["error"]
    finally:
        app.dependency_overrides.clear()
