"""Supply Congestion Index (CI) service.

For any 7-day rolling window w:

    Projected_Volume_w = sum(area_ha * yield_t_per_ha)   for parcels whose harvest date is in w
    CI_w               = Projected_Volume_w / weekly_hub_capacity

    CI > 1.20  -> HIGH_GLUT alert      1.00 < CI <= 1.20 -> WARNING      else NORMAL

Design notes
* One window is evaluated for every day D in the horizon: [D, D+6]. This "sliding" view catches
  clusters that would be split across two calendar-week buckets.
* Two non-overlapping weekly buckets (days 0-6, 7-13) are also returned for dashboards.
* Mature-but-unharvested parcels (harvest date already passed) are folded into day 0 as
  "overdue" standing crop -- they are still going to hit the market.
* The service is pure (no DB / network) so it is trivial to unit test.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Iterable, Optional, Protocol

from app.config import settings
from app.models.schemas import (
    AlertStatus, CongestionForecast, CongestionSummary, DailyForecastPoint, WeeklyBucket,
)
from app.services.gdd_engine import get_crop_profile

WINDOW_DAYS = 7


class ParcelLike(Protocol):
    crop_type: str
    area_hectares: float
    estimated_harvest_date: Optional[dt.date]
    status: str


def parcel_tonnage(parcel: ParcelLike) -> float:
    """Expected marketable tonnage = area x average yield of the parcel's crop."""
    return parcel.area_hectares * get_crop_profile(parcel.crop_type).yield_t_per_ha


def classify(ci: float) -> AlertStatus:
    if ci > settings.high_glut_threshold:
        return AlertStatus.HIGH_GLUT
    if ci > settings.warning_threshold:
        return AlertStatus.WARNING
    return AlertStatus.NORMAL


def build_forecast(
    parcels: Iterable[ParcelLike],
    *,
    hub_name: str,
    weekly_capacity_tons: float,
    today: Optional[dt.date] = None,
    horizon_days: int = 14,
    include_overdue: bool = True,
) -> CongestionForecast:
    if weekly_capacity_tons <= 0:
        raise ValueError("weekly_capacity_tons must be positive")
    today = today or settings.today()

    # ---- 1. Bucket tonnage and parcel counts by harvest day --------------------------------
    tons_by_day: dict[dt.date, float] = defaultdict(float)
    count_by_day: dict[dt.date, int] = defaultdict(int)
    overdue_tons = 0.0
    active = 0
    last_needed_day = today + dt.timedelta(days=horizon_days + WINDOW_DAYS - 2)  # windows reach D+6

    for p in parcels:
        if p.status == "harvested" or p.estimated_harvest_date is None:
            continue
        tons = parcel_tonnage(p)
        day = p.estimated_harvest_date
        if day < today:
            if not include_overdue:
                continue
            overdue_tons += tons
            day = today
        if day > last_needed_day:
            continue  # beyond every window we report
        tons_by_day[day] += tons
        count_by_day[day] += 1
        active += 1

    def window_totals(start: dt.date, days: int) -> tuple[float, int]:
        ds = [start + dt.timedelta(days=i) for i in range(days)]
        return sum(tons_by_day[d] for d in ds), sum(count_by_day[d] for d in ds)

    # ---- 2. Sliding 7-day window starting at each day of the horizon -------------------------
    daily: list[DailyForecastPoint] = []
    for offset in range(horizon_days):
        d = today + dt.timedelta(days=offset)
        vol, n = window_totals(d, WINDOW_DAYS)
        ci = vol / weekly_capacity_tons
        daily.append(DailyForecastPoint(
            date=d,
            harvest_tons_today=round(tons_by_day[d], 2),
            window_start=d,
            window_end=d + dt.timedelta(days=WINDOW_DAYS - 1),
            rolling_7d_tons=round(vol, 2),
            parcels_in_window=n,
            baseline_capacity_tons=weekly_capacity_tons,
            congestion_index=round(ci, 3),
            alert_status=classify(ci),
        ))

    # ---- 3. Non-overlapping weekly buckets ----------------------------------------------------
    buckets: list[WeeklyBucket] = []
    for w in range((horizon_days + WINDOW_DAYS - 1) // WINDOW_DAYS):
        start = today + dt.timedelta(days=w * WINDOW_DAYS)
        length = min(WINDOW_DAYS, horizon_days - w * WINDOW_DAYS)
        vol, n = window_totals(start, length)
        ci = vol / weekly_capacity_tons
        buckets.append(WeeklyBucket(
            label=f"Week {w + 1}", start_date=start, end_date=start + dt.timedelta(days=length - 1),
            projected_tons=round(vol, 2), baseline_capacity_tons=weekly_capacity_tons,
            congestion_index=round(ci, 3), parcel_count=n, alert_status=classify(ci)))

    # ---- 4. Summary ------------------------------------------------------------------------------
    peak = max(daily, key=lambda p: p.congestion_index) if daily else None
    glut_days = [p for p in daily if p.alert_status == AlertStatus.HIGH_GLUT]
    summary = CongestionSummary(
        peak_congestion_index=peak.congestion_index if peak else 0.0,
        peak_window_start=peak.window_start if peak else None,
        high_glut_days=len(glut_days),
        first_high_glut_date=glut_days[0].date if glut_days else None,
        overdue_tons=round(overdue_tons, 2),
        active_parcels=active,
        overall_status=classify(peak.congestion_index) if peak else AlertStatus.NORMAL,
    )
    return CongestionForecast(
        generated_on=today, hub_name=hub_name, baseline_capacity_tons_per_week=weekly_capacity_tons,
        horizon_days=horizon_days, high_glut_threshold=settings.high_glut_threshold,
        summary=summary, weekly_buckets=buckets, daily=daily,
    )
