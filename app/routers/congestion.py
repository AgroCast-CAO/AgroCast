"""Trading-hub capacity + congestion forecast endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models.schemas import CongestionForecast, HubCreate, HubRead
from app.models.tables import Parcel, TradingHubCapacity
from app.routers.parcels import get_weather_service, refresh_parcel
from app.services.congestion_service import build_forecast
from app.services.weather_service import WeatherService

router = APIRouter(prefix="/api", tags=["congestion"])


@router.post("/hubs", response_model=HubRead, status_code=201)
def create_hub(payload: HubCreate, session: Session = Depends(get_session)):
    if session.exec(select(TradingHubCapacity).where(TradingHubCapacity.hub_name == payload.hub_name)).first():
        raise HTTPException(status_code=409, detail="A hub with this name already exists")
    hub = TradingHubCapacity(**payload.model_dump())
    session.add(hub)
    session.commit()
    session.refresh(hub)
    return hub


@router.get("/hubs", response_model=list[HubRead])
def list_hubs(session: Session = Depends(get_session)):
    return session.exec(select(TradingHubCapacity)).all()


@router.get("/congestion-forecast", response_model=CongestionForecast)
def congestion_forecast(
    hub_name: Optional[str] = Query(None, description="Defaults to the first registered hub"),
    horizon_days: int = Query(14, ge=7, le=30),
    refresh: bool = Query(False, description="Recompute every parcel's harvest date from fresh weather first"),
    session: Session = Depends(get_session),
    weather: WeatherService = Depends(get_weather_service),
):
    """14-day forward view: daily/weekly projected harvest tonnage vs hub capacity + glut alert status.

    Prototype assumption: every registered parcel supplies the selected hub.
    """
    query = select(TradingHubCapacity)
    if hub_name:
        query = query.where(TradingHubCapacity.hub_name == hub_name)
    hub = session.exec(query).first()
    if not hub:
        raise HTTPException(status_code=404, detail="Trading hub not found. Create one via POST /api/hubs")

    parcels = session.exec(select(Parcel).where(Parcel.status != "harvested")).all()
    today = settings.today()
    if refresh:
        for parcel in parcels:
            refresh_parcel(parcel, weather, today)
            session.add(parcel)
        session.commit()

    return build_forecast(
        parcels, hub_name=hub.hub_name, weekly_capacity_tons=hub.weekly_absorption_limit_tons,
        today=today, horizon_days=horizon_days)
