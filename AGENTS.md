# AGENTS.md

Web app that takes DJI Mini SE drone video of roads, detects road distress (cracks, potholes, etc.) with a trained YOLO model, and shows the geolocated results on a map. Solo project: keep things simple, no extra infrastructure unless asked.

## Layout

- `web/`: Nuxt (TypeScript) frontend, MapLibre GL map
- `api/`: FastAPI (Python) backend: upload, video processing, detection, results API
- `specs/`: feature specs. Read the relevant one before starting a feature
- `DESIGN.md`: UI rules (Vuetify, dark-only, desktop-first and responsive). Follow it for any UI work

## Versions

- Node 24, pnpm 12 (set in `web/package.json` `packageManager`)
- Python 3.14, managed by `uv` (pinned in `api/.python-version`). Don't rely on the system Python. If a dependency (torch, ultralytics, opencv) has no wheel for 3.14 or fails to install, tell me. The fallback is 3.13. Don't switch versions silently. (Verified: everything installs on 3.14.8. uv's venv currently uses the system 3.14 interpreter.)
- `uv` must be installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`, goes to `~/.local/bin`)
- PostgreSQL + PostGIS via `docker compose` (runs only the DB)
- `ffmpeg` and `ffprobe` must be on PATH
- Pinned on purpose: `typescript@^6` (TS 7 breaks `vue-tsc` and `typescript-eslint`), `vuetify@^3` (DESIGN.md is Vuetify 3), torch and torchvision from the CPU-only index (`[tool.uv.sources]` in `api/pyproject.toml`, avoids ~3 GB of CUDA wheels). Don't bump these without asking.

## Setup (once)

- `cp .env.example .env` and set the values. `docker compose`, the API, and Alembic all read it. It is gitignored.

## Commands

- Web: `cd web && pnpm install`, `pnpm dev` (http://localhost:3000), `pnpm typecheck`, `pnpm lint`
- API: `cd api && uv sync`, `uv run fastapi dev app/main.py` (http://127.0.0.1:8000, `GET /health`), `uv run pytest`, `uv run ruff check --fix . && uv run ruff format .`
- DB: `docker compose up -d db` (waits for the image pull on first run; check with `docker compose ps db`)
- Migrations: `cd api && uv run alembic revision --autogenerate -m "msg"` then `uv run alembic upgrade head`. Needs the DB up and `.env` set. Always read the generated revision before applying it. `alembic/env.py` ignores tables that aren't in our models, because the PostGIS image ships ~40 of its own (tiger, topology) that autogenerate would otherwise drop.
- API types for web: `cd web && pnpm gen:api` (runs `openapi-typescript` against the **running API on :8000**, writes `web/app/types/api.d.ts`)

pnpm blocks dependency build scripts by default. Allowed or denied ones are listed in `web/pnpm-workspace.yaml` (`allowBuilds`). If `pnpm add` fails with `ERR_PNPM_IGNORED_BUILDS`, add the package there as `true` or `false`.

If a command here doesn't exist yet or fails, tell me. Don't invent a replacement.

## Stack choices

- API: SQLAlchemy 2.x (2.1 installed) + GeoAlchemy2 + Alembic, Pydantic schemas, Ultralytics YOLO on CPU
- Web: `maplibre-gl` used directly (no wrapper module)
- Background work: FastAPI `BackgroundTasks` plus a `status` column on the job row. No Celery/Redis.

## Conventions

- TypeScript strict, no `any`. Vue `<script setup lang="ts">`.
- Python type hints everywhere. Route handlers stay thin; logic goes in `api/app/services/`.
- Don't hand-write TS types for API responses. Use the generated ones.
- Config comes from env vars (see `.env.example`). Never hardcode paths or secrets.

## Domain rules (important)

- **Coordinates:** store WGS84 (EPSG:4326). GeoJSON, PostGIS, and MapLibre all use **[longitude, latitude]**. Mixing this up is the most likely bug here.
- **GPS from video:** Mini SE telemetry is a subtitle track. Check for a sidecar `.SRT` first; if there isn't one, extract the embedded subtitle stream with ffmpeg (`ffprobe` to find it). Never assume the format. Parse defensively.
- **Telemetry is coarse.** It can be sparse (about 1 sample/second) and rounded. Interpolate by timestamp for frames in between, and store an accuracy value with each detection instead of treating the point as exact.
- **Position is the drone's, not the road's.** Assume near-straight-down footage unless a spec says otherwise.
- **Don't run YOLO on every frame.** Sample frames, and de-duplicate the same defect across overlapping frames before saving.
- **Distress classes** are defined in one place: `api/app/domain/distress.py`, mapped from the model's class names. Don't invent or rename them elsewhere.
- **Heavy work** (video decode, inference) goes in a plain `def` function run as a background task, never in an `async def` handler (it would block the event loop) and never inside the request. The API returns a job ID; the frontend polls status. On startup, mark jobs left "running" as failed.
- **Map component must be client-only** (`<ClientOnly>` or `.client.vue`). MapLibre breaks under Nuxt SSR.

## Don't touch / never commit

- Never commit `.env`, videos, extracted frames, or model weights (`*.pt`). They live in `api/data/` and `api/models/` (gitignored).
- Never edit an Alembic migration that has already been applied. Create a new one.
- Ask before adding a dependency.

## Workflow

- For any non-trivial feature, read the matching `specs/` file first. If none exists, propose a short plan and wait for approval.
- Before finishing, run the relevant tests/lint and show the output.
- If requirements are ambiguous, ask instead of guessing.
