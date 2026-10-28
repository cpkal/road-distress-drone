import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import get_session_factory
from app.routers.detections import router as detections_router
from app.routers.frames import router as frames_router
from app.routers.uploads import router as uploads_router
from app.services.uploads import recover_interrupted_uploads

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    try:
        session_factory = get_session_factory()
        with session_factory() as db:
            recovered = recover_interrupted_uploads(db)
            if recovered:
                logger.warning(
                    "Marked %d interrupted upload(s) as failed on startup.", recovered
                )
    except SQLAlchemyError as exc:
        logger.warning("Skipping startup upload recovery: %s", exc)
    yield


app = FastAPI(title="web-drone API", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(uploads_router)
app.include_router(frames_router)
app.include_router(detections_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
