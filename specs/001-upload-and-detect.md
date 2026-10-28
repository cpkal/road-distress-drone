# Spec 001: Upload media and detect road distress

Status: draft. Read AGENTS.md and DESIGN.md first.

## Goal

The user uploads a DJI drone video (primary option) or drone photos with metadata (second option). The system extracts the location, runs the YOLO model for object detection (bounding boxes and distress classes only—no severity), and saves the detections with their location. Showing them on the map is spec 002, not this one.

## Flow

1. The user uploads **one video** (optionally with its sidecar `.SRT` file) **or one or more drone photos**.
2. The API streams the files to `{DATA_DIR}/uploads/<upload_id>/`, creates an `upload` record with status `queued`, and returns its ID right away.
3. A background job sets status to `processing`, then does: read metadata → produce frames → run YOLO → de-duplicate → save detections → set status to `done` (or `failed` with an error message).
4. The frontend lists uploads with status and progress.

## API

- `POST /uploads`: multipart upload (`files: list[UploadFile]`). Infers `kind` (`video` | `photos`) from file extensions. Returns `{ id, status }`.
- `GET /uploads`: list uploads (newest first) with status (`queued` | `processing` | `done` | `failed`), progress (`0`–`100`), counts (`frame_count`, `detection_count`), and any warnings.
- `GET /uploads/{id}`: one upload's details (same fields plus `error`).
- `GET /uploads/{id}/detections`: GeoJSON `FeatureCollection`, `[lon, lat]` order. Detections with no location have `geometry: null`. Each feature's `properties` has `id`, `class`, `confidence`, `bbox` (`[x_min, y_min, x_max, y_max]` normalized `0..1`), `frame_id`, `image_url` (`/frames/{frame_id}/image`), `timestamp`, `location_accuracy_m`, and `oblique`. (No severity field.)
- `GET /frames/{id}/image`: the saved clean frame image (used later by the detail view to overlay bounding boxes).
- Enable `CORSMiddleware` so the Nuxt frontend (`NUXT_PUBLIC_API_BASE`) can call the API directly.

## Data (minimum fields; the agent proposes the exact schema in its plan)

- **upload:** `id`, `kind` (`video` | `photos`), `status` (`queued` | `processing` | `done` | `failed`), `progress` (`0`–`100`), `error` (nullable), `warnings` (list of strings), `created_at`
- **frame:** `id`, `upload_id`, `source_file`, `timestamp` (video seconds as float or photo capture time / sequence index), `location` (`Point`, EPSG:4326, nullable), `location_accuracy_m` (nullable), `gimbal_pitch_deg` (nullable), `oblique` (bool), `image_path` (nullable; saved only if the frame retains at least one detection after de-duplication)
- **detection:** `id`, `frame_id`, `class`, `confidence`, `bbox` (normalized `[x_min, y_min, x_max, y_max]` in `0.0..1.0`), `location` (`Point`, EPSG:4326, nullable, copied from the frame)

## Behavior

**Video (primary option)**
- Accept `.mp4` / `.mov` (1 video per upload, plus an optional `.srt` sidecar). Use the uploaded `.SRT` if present, otherwise extract the embedded subtitle stream with `ffmpeg` (`ffprobe` to find it; see AGENTS.md). If neither exists, process the video with `location = null` and add a warning.
- Parse DJI `.SRT` telemetry defensively (support common DJI formats such as `[latitude: ...] [longitude: ...]` and `GPS (lon, lat, alt)`). Ignore samples where coordinates are `(0, 0)` (no GPS lock).
- Sample frames at `FRAME_SAMPLE_FPS` (default `1.0` per second; videos shorter than `1s` still sample at least frame `t = 0.0`).
- Interpolate each sampled frame's position linearly from surrounding telemetry timestamps. If a frame timestamp falls within `1.0s` before the first or after the last telemetry sample, clamp to the nearest sample; otherwise set `location = null`.

**Photos (second option, when uploading drone images with metadata instead of video)**
- Accept `.jpg` / `.jpeg` (1 to `MAX_PHOTOS_PER_UPLOAD` photos per upload). Each photo is one frame.
- Read metadata with `exiftool -j -n` (signed decimal degrees) as defined in AGENTS.md: check standard EXIF GPS (`GPSLatitude`, `GPSLongitude`) first, then fall back to DJI XMP (`GpsLatitude`, `GpsLongitude`). Missing GPS or `(0, 0)` coordinates produce a frame with `location = null`, plus a warning on the upload.
- Read `GimbalPitchDegree` if present. If missing, leave `gimbal_pitch_deg = null` and `oblique = false` (assume near-straight-down per AGENTS.md). If present and more than `20°` away from `-90°` (straight down), set `oblique = true` and treat the location as lower accuracy.

**Location Accuracy (`location_accuracy_m`)**
- If `location` is `null`: `location_accuracy_m = null`.
- If `oblique = true`: `15.0` meters.
- Otherwise: `3.0` meters for direct GPS (photo or exact telemetry timestamp match) and `5.0` meters for interpolated video frames.

**Detection (Object Detection Bounding Boxes Only)**
- Model path from `MODEL_PATH` (default `{MODELS_DIR}/best.pt`, i.e. `models/best.pt` relative to `api/`). CPU inference. Minimum confidence from `MIN_CONFIDENCE` (default `0.25`).
- Map model class names through `api/app/domain/distress.py` (matched in `web/app/constants/distress.ts`). Temporary canonical classes (accepting space- or underscore-separated model labels, case-insensitively):
  - `transverse_cracking` ("Transverse cracking")
  - `longitudinal_cracking` ("Longitudinal cracking")
  - `alligator_cracking` ("Alligator cracking")
  - `patch` ("Patch")
  - `pothole` ("Pothole")
  An unknown class name fails the job with a clear error. Don't invent classes.
- Store only class, confidence, and normalized bounding box `[x_min, y_min, x_max, y_max]`. There is no severity level or severity scoring.
- De-duplicate: across consecutive sampled frames (or consecutive photos ordered by capture time), detections of the same class within `DEDUPE_RADIUS_M` (default `5.0` meters) are merged transitively while the defect stays in range, keeping the single detection with the highest confidence. Never merge two detections from the *same* frame. Frames with `location = null` aren't de-duplicated.
- Save the clean (unannotated) frame image to disk only when the frame has at least one surviving detection after de-duplication.

## Edge cases

- Unsupported file type, mixed video + photo upload, multiple videos, or standalone `.SRT` without a video: reject with a clear `400` / `422` error and message, and clean up any partial files.
- Corrupt or unreadable file, or missing model weights at runtime: the job ends `failed` with the reason in `error`. It never crashes the server.
- Limits (configurable via env vars): `MAX_VIDEO_SIZE_MB` (default `2048` MB / 2 GB per video, enforced while streaming chunks to disk) and `MAX_PHOTOS_PER_UPLOAD` (default `100` photos per upload).
- The server restarts mid-job: any upload left in `queued` or `processing` is marked `failed` on startup (see AGENTS.md).
- Uploading the same file twice is allowed. There is no cross-upload de-duplication.

## Out of scope

- The map UI (spec 002), user accounts, deleting or editing detections, severity levels/scoring, model training, RAW/DNG files, exporting reports.

## Build order

Do these one at a time. After each one, run tests and lint, then stop for my review.

1. Database schema, migrations, startup recovery (`queued`/`processing` → `failed`), `POST /uploads` and `GET /uploads` / `GET /uploads/{id}` (files streamed & saved, status tracked, no processing yet).
2. Photo metadata extraction with `exiftool`, plus tests.
3. Video telemetry extraction (`.SRT` or embedded) with interpolation, plus tests.
4. Frame sampling, YOLO inference, de-duplication, and saving detections.
5. `GET /uploads/{id}/detections` (GeoJSON) and `GET /frames/{id}/image`.
6. Minimal Uploads page per DESIGN.md: upload button, list with status chips and progress. No map.

## Acceptance criteria

- `uv run pytest`, `ruff`, `pnpm typecheck`, and `pnpm lint` all pass.
- Tests use fixtures in `api/tests/fixtures/` (one short video with its SRT, one photo with GPS, one photo without GPS) and a **fake model**, so they don't run real YOLO.
- Manual check with the real model: upload a real video, and the upload reaches `done` with detections saved. Pick one detection's coordinates and confirm it lands near the right road in a map app.
- A photo with GPS gives a location that matches `exiftool`'s output. A photo without GPS gives `geometry: null` and a warning.
- The GeoJSON is valid and uses `[lon, lat]` order.
