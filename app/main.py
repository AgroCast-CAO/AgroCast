"""AgroCast API entrypoint.   Run:  uvicorn app.main:app --reload"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import init_db
from app.routers import congestion, parcels


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
