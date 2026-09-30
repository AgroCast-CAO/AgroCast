"""SQLModel / SQLAlchemy table definitions."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import NaiveDatetime
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> dt.datetime:  # naive UTC (portable across SQLite/PostgreSQL)
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Parcel(SQLModel, table=True):
    """A registered farm plot."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    owner: str
    crop_type: str = Field(default="cabbage", index=True)
    area_hectares: float
    planting_date: dt.date = Field(index=True)
    latitude: float
    longitude: float
    geometry_geojson: Optional[str] = None          # GeoJSON Polygon as text (optional)
    estimated_harvest_date: Optional[dt.date] = Field(default=None, index=True)
    harvest_basis: Optional[str] = None              # observed_forecast | climatology
    status: str = Field(default="growing", index=True)  # planned | growing | mature | harvested
    created_at: NaiveDatetime = Field(default_factory=_utcnow)


class DailyWeatherLog(SQLModel, table=True):
    """Cache of daily temperatures per grid point (lat/lon rounded to 2 decimals).

    calculated_gdd uses the default crop's T_base (cabbage, 4.4 C) for convenient inspection;
    the GDD engine always recomputes with the parcel's own crop profile.
    """
    __table_args__ = (UniqueConstraint("date", "latitude", "longitude", name="uq_weather_day_point"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    date: dt.date = Field(index=True)
    latitude: float = Field(index=True)
    longitude: float = Field(index=True)
    t_max: float
    t_min: float
    calculated_gdd: float
    source: str = "archive"                          # archive | forecast
    fetched_at: NaiveDatetime = Field(default_factory=_utcnow)


class TradingHubCapacity(SQLModel, table=True):
    """Absorption capacity of a trading post."""
    id: Optional[int] = Field(default=None, primary_key=True)
    hub_name: str = Field(index=True, unique=True)
    weekly_absorption_limit_tons: float
