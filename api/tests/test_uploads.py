import io
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db, get_session_factory
from app.main import app
from app.models.upload import Detection, Frame, Upload, UploadKind, UploadStatus
from app.routers.uploads import get_job_runner
from app.services.uploads import recover_interrupted_uploads


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
            "max_video_size_mb": 1,
            "max_photos_per_upload": 3,
        }
    )


@pytest.fixture
def client(db_session: Session, test_settings: Settings) -> Generator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_job_runner] = lambda: lambda *_args, **_kwargs: None
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_upload_video_with_srt(client: TestClient, test_settings: Settings) -> None:
    files = [
        ("files", ("flight_01.MP4", io.BytesIO(b"fake-mp4-bytes"), "video/mp4")),
        (
            "files",
            (
                "flight_01.SRT",
                io.BytesIO(b"1\n00:00:00,000 --> 00:00:01,000\n"),
                "text/plain",
            ),
        ),
    ]
    response = client.post("/uploads", files=files)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    upload_id = body["id"]

    upload_dir = Path(test_settings.data_dir) / "uploads" / upload_id
    assert (upload_dir / "flight_01.MP4").read_bytes() == b"fake-mp4-bytes"
    assert (upload_dir / "flight_01.SRT").exists()

    detail_resp = client.get(f"/uploads/{upload_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["id"] == upload_id
    assert detail["kind"] == "video"
    assert detail["status"] == "queued"
    assert detail["progress"] == 0
    assert detail["frame_count"] == 0
    assert detail["detection_count"] == 0
    assert detail["warnings"] == []
    assert detail["error"] is None


def test_upload_photos_and_list_order(
    client: TestClient,
    db_session: Session,
    test_settings: Settings,
) -> None:
    resp1 = client.post(
        "/uploads",
        files=[
            ("files", ("img1.jpg", io.BytesIO(b"jpeg-1"), "image/jpeg")),
            ("files", ("img2.jpeg", io.BytesIO(b"jpeg-2"), "image/jpeg")),
        ],
    )
    assert resp1.status_code == 201
    id1 = uuid.UUID(resp1.json()["id"])

    frame = Frame(
        upload_id=id1,
        source_file="img1.jpg",
        timestamp=0.0,
        location=None,
        oblique=False,
    )
    db_session.add(frame)
    db_session.flush()
    db_session.add(
        Detection(
            frame_id=frame.id,
            class_name="pothole",
            confidence=0.88,
            bbox=[0.1, 0.2, 0.3, 0.4],
            location=None,
        )
    )
    db_session.commit()

    resp2 = client.post(
        "/uploads",
        files=[("files", ("clip.mov", io.BytesIO(b"mov-bytes"), "video/quicktime"))],
    )
    assert resp2.status_code == 201
    id2 = uuid.UUID(resp2.json()["id"])

    list_resp = client.get("/uploads")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert [uuid.UUID(item["id"]) for item in items] == [id2, id1]
    assert items[0]["kind"] == "video"
    assert items[1]["kind"] == "photos"
    assert items[1]["frame_count"] == 1
    assert items[1]["detection_count"] == 1

    upload_dir = Path(test_settings.data_dir) / "uploads" / str(id1)
    assert (upload_dir / "img1.jpg").read_bytes() == b"jpeg-1"
    assert (upload_dir / "img2.jpeg").read_bytes() == b"jpeg-2"


@pytest.mark.parametrize(
    ("files", "expected_msg"),
    [
        (
            [("files", ("notes.txt", io.BytesIO(b"hello"), "text/plain"))],
            "Unsupported file type",
        ),
        (
            [
                ("files", ("clip.mp4", io.BytesIO(b"mp4"), "video/mp4")),
                ("files", ("photo.jpg", io.BytesIO(b"jpg"), "image/jpeg")),
            ],
            "Cannot mix",
        ),
        (
            [
                ("files", ("clip1.mp4", io.BytesIO(b"mp4"), "video/mp4")),
                ("files", ("clip2.mp4", io.BytesIO(b"mp4"), "video/mp4")),
            ],
            "exactly one video",
        ),
        (
            [("files", ("telemetry.srt", io.BytesIO(b"srt"), "text/plain"))],
            "exactly one video",
        ),
        (
            [
                ("files", ("p1.jpg", io.BytesIO(b"1"), "image/jpeg")),
                ("files", ("p2.jpg", io.BytesIO(b"2"), "image/jpeg")),
                ("files", ("p3.jpg", io.BytesIO(b"3"), "image/jpeg")),
                ("files", ("p4.jpg", io.BytesIO(b"4"), "image/jpeg")),
            ],
            "Too many photos",
        ),
        (
            [("files", ("empty.mp4", io.BytesIO(b""), "video/mp4"))],
            "empty",
        ),
    ],
)
def test_upload_validation_rejects_invalid_inputs(
    client: TestClient,
    test_settings: Settings,
    files: list[tuple[str, tuple[str, io.BytesIO, str]]],
    expected_msg: str,
) -> None:
    response = client.post("/uploads", files=files)
    assert response.status_code == 400
    assert expected_msg in response.json()["detail"]

    uploads_root = Path(test_settings.data_dir) / "uploads"
    if uploads_root.exists():
        assert list(uploads_root.iterdir()) == []


def test_upload_rejects_oversized_video_and_cleans_up(
    client: TestClient,
    test_settings: Settings,
) -> None:
    # test_settings sets max_video_size_mb = 1
    oversized = b"x" * (1024 * 1024 + 64)
    response = client.post(
        "/uploads",
        files=[("files", ("big.mp4", io.BytesIO(oversized), "video/mp4"))],
    )
    assert response.status_code == 400
    assert "exceeds maximum size" in response.json()["detail"]

    uploads_root = Path(test_settings.data_dir) / "uploads"
    if uploads_root.exists():
        assert list(uploads_root.iterdir()) == []


def test_get_upload_not_found(client: TestClient) -> None:
    missing_id = uuid.uuid4()
    response = client.get(f"/uploads/{missing_id}")
    assert response.status_code == 404


def test_startup_recovery_marks_queued_and_processing_as_failed(
    db_session: Session,
) -> None:
    u_queued = Upload(
        kind=UploadKind.VIDEO.value, status=UploadStatus.QUEUED.value, progress=0
    )
    u_proc = Upload(
        kind=UploadKind.PHOTOS.value, status=UploadStatus.PROCESSING.value, progress=45
    )
    u_done = Upload(
        kind=UploadKind.VIDEO.value, status=UploadStatus.DONE.value, progress=100
    )
    db_session.add_all([u_queued, u_proc, u_done])
    db_session.commit()

    recovered_count = recover_interrupted_uploads(db_session)
    assert recovered_count == 2

    db_session.refresh(u_queued)
    db_session.refresh(u_proc)
    db_session.refresh(u_done)

    assert u_queued.status == UploadStatus.FAILED.value
    assert u_queued.error is not None
    assert u_proc.status == UploadStatus.FAILED.value
    assert u_proc.error is not None
    assert u_done.status == UploadStatus.DONE.value
    assert u_done.error is None
