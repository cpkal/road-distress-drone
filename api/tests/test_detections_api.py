import uuid
from collections.abc import Generator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from geoalchemy2 import WKTElement
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db, get_session_factory
from app.main import app
from app.models.upload import Detection, Frame, Upload
from app.services.detector import DetectorProtocol, RawPrediction, get_detector

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_VIDEO = FIXTURES_DIR / "sample_video.mp4"
SAMPLE_SRT = FIXTURES_DIR / "sample_video.srt"
PHOTO_WITH_GPS = FIXTURES_DIR / "photo_with_gps.jpg"
PHOTO_WITHOUT_GPS = FIXTURES_DIR / "photo_without_gps.jpg"


class _StaticFakeDetector(DetectorProtocol):
    def __init__(self, predictions_by_call: list[Sequence[RawPrediction]]) -> None:
        self._predictions_by_call = predictions_by_call
        self._idx = 0

    def predict(self, image_path: Path) -> Sequence[RawPrediction]:
        assert image_path.is_file()
        if self._idx < len(self._predictions_by_call):
            preds = self._predictions_by_call[self._idx]
        else:
            preds = []
        self._idx += 1
        return preds


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


def test_detections_geojson_and_frame_image_endpoints(
    db_session: Session,
    test_settings: Settings,
) -> None:
    detector = _StaticFakeDetector(
        [
            [RawPrediction("pothole", 0.93, [0.1, 0.2, 0.4, 0.5])],
            [RawPrediction("patch", 0.78, [0.5, 0.5, 0.9, 0.9])],
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

            geojson_resp = client.get(f"/uploads/{upload_id}/detections")
            assert geojson_resp.status_code == 200
            fc = geojson_resp.json()
            assert fc["type"] == "FeatureCollection"
            assert len(fc["features"]) == 2

            by_class = {feat["properties"]["class"]: feat for feat in fc["features"]}
            assert set(by_class.keys()) == {"pothole", "patch"}

            # Feature from photo_with_gps.jpg has [lon, lat] Point geometry
            f_gps = by_class["pothole"]
            assert f_gps["type"] == "Feature"
            assert f_gps["geometry"] is not None
            assert f_gps["geometry"]["type"] == "Point"
            lon, lat = f_gps["geometry"]["coordinates"]
            assert lon == pytest.approx(100.501762, abs=1e-6)
            assert lat == pytest.approx(13.756331, abs=1e-6)
            assert "severity" not in f_gps["properties"]
            assert f_gps["properties"]["confidence"] == pytest.approx(0.93)
            assert f_gps["properties"]["bbox"] == pytest.approx([0.1, 0.2, 0.4, 0.5])
            assert f_gps["properties"]["location_accuracy_m"] == pytest.approx(3.0)
            assert f_gps["properties"]["oblique"] is False

            # Feature from photo_without_gps.jpg has geometry: null
            f_no_gps = by_class["patch"]
            assert f_no_gps["geometry"] is None
            assert "severity" not in f_no_gps["properties"]
            assert f_no_gps["properties"]["location_accuracy_m"] is None

            # Fetch saved frame image via image_url
            img_url = f_gps["properties"]["image_url"]
            img_resp = client.get(img_url)
            assert img_resp.status_code == 200
            assert img_resp.headers["content-type"] == "image/jpeg"
            assert img_resp.content.startswith(b"\xff\xd8")
    finally:
        app.dependency_overrides.clear()


def test_frame_without_detections_and_missing_ids_return_404(
    db_session: Session,
    test_settings: Settings,
) -> None:
    # Frame 0 has 1 detection, Frame 1 and Frame 2 have 0 detections
    detector = _StaticFakeDetector(
        [
            [RawPrediction("longitudinal cracking", 0.81, [0.2, 0.1, 0.6, 0.9])],
            [],
            [],
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
                        ("sample_video.mp4", SAMPLE_VIDEO.read_bytes(), "video/mp4"),
                    ),
                    (
                        "files",
                        ("sample_video.srt", SAMPLE_SRT.read_bytes(), "text/plain"),
                    ),
                ],
            )
            assert resp.status_code == 201
            upload_id = resp.json()["id"]

            frames = list(
                db_session.scalars(
                    select(Frame)
                    .where(Frame.upload_id == upload_id)
                    .order_by(Frame.timestamp.asc())
                )
            )
            assert len(frames) == 3

            # Frame 0 has a detection -> image exists (200)
            r0 = client.get(f"/frames/{frames[0].id}/image")
            assert r0.status_code == 200

            # Frame 1 had no detections -> image was not saved (404)
            r1 = client.get(f"/frames/{frames[1].id}/image")
            assert r1.status_code == 404

            # Unknown frame ID and unknown upload ID -> 404
            assert client.get(f"/frames/{uuid.uuid4()}/image").status_code == 404
            assert client.get(f"/uploads/{uuid.uuid4()}/detections").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_map_detections_filters_null_exclusion_and_detail(
    db_session: Session,
    test_settings: Settings,
) -> None:
    detector = _StaticFakeDetector(
        [
            # Upload 1: photo_with_gps (2 detections) + photo_without_gps (1 detection, no location)
            [
                RawPrediction("pothole", 0.91, [0.1, 0.2, 0.3, 0.4]),
                RawPrediction("alligator_cracking", 0.84, [0.5, 0.6, 0.7, 0.8]),
            ],
            [RawPrediction("patch", 0.77, [0.2, 0.2, 0.5, 0.5])],
            # Upload 2: sample_video (1 detection on frame 0)
            [RawPrediction("transverse_cracking", 0.88, [0.15, 0.25, 0.45, 0.65])],
            [],
            [],
        ]
    )
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_detector] = lambda: detector

    try:
        with TestClient(app) as client:
            r1 = client.post(
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
            assert r1.status_code == 201
            upload_1_id = r1.json()["id"]

            r2 = client.post(
                "/uploads",
                files=[
                    (
                        "files",
                        ("sample_video.mp4", SAMPLE_VIDEO.read_bytes(), "video/mp4"),
                    ),
                    (
                        "files",
                        ("sample_video.srt", SAMPLE_SRT.read_bytes(), "text/plain"),
                    ),
                ],
            )
            assert r2.status_code == 201
            upload_2_id = r2.json()["id"]

            # 1) Unfiltered GET /detections excludes the null-location "patch" detection
            all_resp = client.get("/detections")
            assert all_resp.status_code == 200
            all_fc = all_resp.json()
            assert all_fc["type"] == "FeatureCollection"
            assert all_fc["truncated"] is False
            assert len(all_fc["features"]) == 3

            classes_returned = {f["properties"]["class"] for f in all_fc["features"]}
            assert classes_returned == {
                "pothole",
                "alligator_cracking",
                "transverse_cracking",
            }
            assert "patch" not in classes_returned

            # Verify [lon, lat] order on pothole from photo_with_gps.jpg
            pothole_feat = next(
                f for f in all_fc["features"] if f["properties"]["class"] == "pothole"
            )
            lon, lat = pothole_feat["geometry"]["coordinates"]
            assert lon == pytest.approx(100.501762, abs=1e-6)
            assert lat == pytest.approx(13.756331, abs=1e-6)
            assert pothole_feat["properties"]["upload_id"] == upload_1_id
            assert "severity" not in pothole_feat["properties"]

            # 2) Filter by repeatable class query param
            cls_resp = client.get(
                "/detections",
                params=[("class", "pothole"), ("class", "transverse_cracking")],
            )
            assert cls_resp.status_code == 200
            cls_fc = cls_resp.json()
            assert len(cls_fc["features"]) == 2
            assert {f["properties"]["class"] for f in cls_fc["features"]} == {
                "pothole",
                "transverse_cracking",
            }

            # 3) Filter by upload_id
            up_resp = client.get("/detections", params={"upload_id": upload_2_id})
            assert up_resp.status_code == 200
            up_fc = up_resp.json()
            assert len(up_fc["features"]) == 1
            assert up_fc["features"][0]["properties"]["class"] == "transverse_cracking"
            assert up_fc["features"][0]["properties"]["upload_id"] == upload_2_id

            # 4) Filter by non-existent upload_id returns empty match, not an error
            missing_up_resp = client.get(
                "/detections", params={"upload_id": str(uuid.uuid4())}
            )
            assert missing_up_resp.status_code == 200
            assert missing_up_resp.json()["features"] == []
            assert missing_up_resp.json()["truncated"] is False

            # 5) GET /detections/{id} returns full details
            det_id = pothole_feat["properties"]["id"]
            detail_resp = client.get(f"/detections/{det_id}")
            assert detail_resp.status_code == 200
            detail = detail_resp.json()
            assert detail["id"] == det_id
            assert detail["class"] == "pothole"
            assert "severity" not in detail
            assert detail["confidence"] == pytest.approx(0.91)
            assert detail["bbox"] == pytest.approx([0.1, 0.2, 0.3, 0.4])
            assert detail["location"]["type"] == "Point"
            assert detail["location"]["coordinates"][0] == pytest.approx(
                100.501762, abs=1e-6
            )
            assert detail["location"]["coordinates"][1] == pytest.approx(
                13.756331, abs=1e-6
            )
            assert detail["location_accuracy_m"] == pytest.approx(3.0)
            assert detail["oblique"] is False
            assert detail["source_file"] == "photo_with_gps.jpg"
            assert detail["upload_id"] == upload_1_id
            assert detail["upload_kind"] == "photos"
            assert detail["image_url"].startswith("/frames/")
            assert detail["image_url"].endswith("/image")
            assert len(detail["frame_detections"]) == 2
            assert [d["class"] for d in detail["frame_detections"]] == [
                "pothole",
                "alligator_cracking",
            ]

            # Unknown detection ID -> 404
            assert client.get(f"/detections/{uuid.uuid4()}").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_map_detections_10000_cap_sets_truncated(
    db_session: Session,
    test_settings: Settings,
) -> None:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: test_settings

    try:
        upload = Upload(
            id=uuid.uuid4(),
            kind="photos",
            status="done",
            progress=100,
            warnings=[],
        )
        db_session.add(upload)
        db_session.flush()

        point = WKTElement("POINT(100.501762 13.756331)", srid=4326)
        frame = Frame(
            id=uuid.uuid4(),
            upload_id=upload.id,
            source_file="bulk.jpg",
            timestamp=1700000000.0,
            location=point,
            location_accuracy_m=3.0,
            oblique=False,
            image_path=None,
        )
        db_session.add(frame)
        db_session.flush()

        rows = [
            {
                "id": uuid.uuid4(),
                "frame_id": frame.id,
                "class_name": "pothole",
                "confidence": 0.9,
                "bbox": [0.1, 0.1, 0.2, 0.2],
                "location": point,
            }
            for _ in range(10_005)
        ]
        db_session.execute(insert(Detection), rows)
        db_session.commit()

        with TestClient(app) as client:
            resp = client.get("/detections")
            assert resp.status_code == 200
            fc = resp.json()
            assert len(fc["features"]) == 10_000
            assert fc["truncated"] is True
    finally:
        app.dependency_overrides.clear()
