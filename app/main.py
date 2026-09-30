"""AgroCast API entrypoint.   Run:  uvicorn app.main:app --reload

The dashboard at /frontend/index.html is served by this same app (StaticFiles mount below),
so the browser JS can call /api/... with no CORS setup needed.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.routers import congestion, parcels

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()   # create tables on startup
    yield


app = FastAPI(
    title="AgroCast API",
    description="Predictive early-warning platform: GDD-based harvest forecasting + Supply Congestion Index.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(parcels.router)
app.include_router(congestion.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "weather_mode": settings.weather_mode, "today": settings.today().isoformat()}


# Mounted last and at "/" so it never shadows the /api/* and /health routes above:
# FastAPI matches the explicit routes registered first before falling through to this mount.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
