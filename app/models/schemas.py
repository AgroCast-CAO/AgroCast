"""Pydantic request/response schemas (API contract)."""
from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


# ----------------------------- Parcels -----------------------------
class ParcelCreate(BaseModel):
    name: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    crop_type: str = "cabbage"
    area_hectares: float = Field(gt=0, le=1000)
    planting_date: dt.date
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    geometry_geojson: Optional[dict[str, Any]] = Field(
        default=None, description="Optional GeoJSON Polygon. If lat/lon are omitted, its centroid is used."
    )

    @model_validator(mode="after")
    def _resolve_location(self) -> "ParcelCreate":
        if self.geometry_geojson is not None:
            if self.geometry_geojson.get("type") != "Polygon":
                raise ValueError("geometry_geojson must be a GeoJSON Polygon")
        if self.latitude is None or self.longitude is None:
            if self.geometry_geojson is None:
                raise ValueError("Provide latitude+longitude, or a geometry_geojson polygon")
            try:
                ring = self.geometry_geojson["coordinates"][0]
                self.longitude = sum(p[0] for p in ring) / len(ring)   # simple vertex-mean centroid
                self.latitude = sum(p[1] for p in ring) / len(ring)
            except (KeyError, IndexError, TypeError, ZeroDivisionError) as exc:
                raise ValueError("Malformed GeoJSON polygon") from exc
        return self


class ParcelRead(BaseModel):
    id: int
    name: str
    owner: str
    crop_type: str
    area_hectares: float
    planting_date: dt.date
    latitude: float
    longitude: float
    geometry_geojson: Optional[dict[str, Any]] = None
    estimated_harvest_date: Optional[dt.date] = None
    harvest_basis: Optional[str] = None
    status: str
    # --- live growth metrics ---
    accumulated_gdd: float
    target_gdd: float
    progress_pct: float
    days_to_harvest: Optional[int] = None
    expected_tonnage: float


class HubCreate(BaseModel):
    hub_name: str = Field(min_length=1)
    weekly_absorption_limit_tons: float = Field(gt=0)


class HubRead(HubCreate):
    id: int


# ------------------------- Congestion forecast -------------------------
class AlertStatus(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"          # 1.0 < CI <= 1.2 : at/over capacity
    HIGH_GLUT = "HIGH_GLUT"      # CI > 1.2       : trigger alert


class DailyForecastPoint(BaseModel):
    date: dt.date
    harvest_tons_today: float = Field(description="Tonnage of parcels maturing on this exact day")
    window_start: dt.date
    window_end: dt.date
    rolling_7d_tons: float = Field(description="Projected volume maturing in [date, date+6]")
    parcels_in_window: int
    baseline_capacity_tons: float = Field(description="Hub weekly absorption limit")
    congestion_index: float
    alert_status: AlertStatus


class WeeklyBucket(BaseModel):
    label: str
    start_date: dt.date
    end_date: dt.date
    projected_tons: float
    baseline_capacity_tons: float
    congestion_index: float
    parcel_count: int
    alert_status: AlertStatus


class CongestionSummary(BaseModel):
    peak_congestion_index: float
    peak_window_start: Optional[dt.date]
    high_glut_days: int
    first_high_glut_date: Optional[dt.date]
    overdue_tons: float = Field(description="Mature-but-unharvested tonnage folded into day 0")
    active_parcels: int
    overall_status: AlertStatus


class CongestionForecast(BaseModel):
    generated_on: dt.date
    hub_name: str
    baseline_capacity_tons_per_week: float
    horizon_days: int
    high_glut_threshold: float
    summary: CongestionSummary
    weekly_buckets: list[WeeklyBucket]
    daily: list[DailyForecastPoint]
