"""Parcel endpoints: register plots and view live growth progress."""
from __future__ import annotations

import datetime as dt
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models.schemas import ParcelCreate, ParcelRead
from app.models.tables import Parcel
from app.services.congestion_service import parcel_tonnage
from app.services.gdd_engine import GrowthProjection, estimate_growth, get_crop_profile
from app.services.weather_service import WeatherService

router = APIRouter(prefix="/api/parcels", tags=["parcels"])


def get_weather_service(session: Session = Depends(get_session)) -> WeatherService:
    return WeatherService(session)


def apply_projection(parcel: Parcel, proj: GrowthProjection, today: dt.date) -> None:
    """Copy projection results onto the ORM object (harvested parcels keep their status)."""
    parcel.estimated_harvest_date = proj.harvest_date
    parcel.harvest_basis = proj.basis
    if parcel.status != "harvested":
        if parcel.planting_date > today:
            parcel.status = "planned"
        elif proj.progress_pct >= 100:
            parcel.status = "mature"
        else:
            parcel.status = "growing"


def to_read(parcel: Parcel, proj: GrowthProjection) -> ParcelRead:
    data = parcel.model_dump()
    data["geometry_geojson"] = json.loads(parcel.geometry_geojson) if parcel.geometry_geojson else None
    return ParcelRead(
        **data,
        accumulated_gdd=proj.accumulated_gdd,
        target_gdd=proj.target_gdd,
        progress_pct=proj.progress_pct,
        days_to_harvest=proj.days_to_harvest,
        expected_tonnage=round(parcel_tonnage(parcel), 2),
    )


def refresh_parcel(parcel: Parcel, weather: WeatherService, today: dt.date) -> GrowthProjection:
    proj = estimate_growth(
        weather, planting_date=parcel.planting_date, latitude=parcel.latitude,
        longitude=parcel.longitude, crop_type=parcel.crop_type, today=today)
    apply_projection(parcel, proj, today)
    return proj


@router.post("", response_model=ParcelRead, status_code=201)
def register_parcel(
    payload: ParcelCreate,
    session: Session = Depends(get_session),
    weather: WeatherService = Depends(get_weather_service),
):
    """Register a plot; its harvest date is computed automatically from GDD."""
    try:
        get_crop_profile(payload.crop_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    parcel = Parcel(
        name=payload.name, owner=payload.owner, crop_type=payload.crop_type.lower(),
        area_hectares=payload.area_hectares, planting_date=payload.planting_date,
        latitude=payload.latitude, longitude=payload.longitude,
        geometry_geojson=json.dumps(payload.geometry_geojson) if payload.geometry_geojson else None,
    )
    proj = refresh_parcel(parcel, weather, settings.today())
    if proj.harvest_date is None:
        raise HTTPException(status_code=422, detail="Could not project a harvest date for this parcel")
    session.add(parcel)
    session.commit()
    session.refresh(parcel)
    return to_read(parcel, proj)


@router.get("", response_model=list[ParcelRead])
def list_parcels(
    status: Optional[str] = Query(None, description="Filter: planned | growing | mature | harvested"),
    refresh: bool = Query(True, description="Recompute GDD progress / harvest dates with the latest weather"),
    session: Session = Depends(get_session),
    weather: WeatherService = Depends(get_weather_service),
):
    """List parcels with current growth progress (% of target GDD reached)."""
    query = select(Parcel).order_by(Parcel.planting_date)
    if status:
        query = query.where(Parcel.status == status)
    parcels = session.exec(query).all()
    today = settings.today()

    out = []
    for parcel in parcels:
        proj = refresh_parcel(parcel, weather, today)   # weather is DB-cached, so this is cheap
        if refresh:
            session.add(parcel)                          # persist updated harvest date / status
        out.append(to_read(parcel, proj))
    if refresh:
        session.commit()
    return out


@router.post("/{parcel_id}/harvest", response_model=ParcelRead)
def mark_harvested(
    parcel_id: int,
    session: Session = Depends(get_session),
    weather: WeatherService = Depends(get_weather_service),
):
    """Mark a parcel harvested so it no longer counts toward projected supply."""
    parcel = session.get(Parcel, parcel_id)
    if not parcel:
        raise HTTPException(status_code=404, detail="Parcel not found")
    parcel.status = "harvested"
    proj = estimate_growth(weather, planting_date=parcel.planting_date, latitude=parcel.latitude,
                           longitude=parcel.longitude, crop_type=parcel.crop_type)
    session.add(parcel)
    session.commit()
    session.refresh(parcel)
    return to_read(parcel, proj)
