"""Open-Meteo weather pipeline with DB caching.

Data sources (both free, no API key):
    * Archive  API (ERA5 reanalysis)  -> confirmed past days (lags real time ~5 days)
    * Forecast API                    -> the last few days + up to 16 days ahead

Strategy for a requested [start, end] range:
    1. Read whatever is already cached in `DailyWeatherLog`.
    2. Re-fetch only missing days, plus *forecast* rows that are stale (older than the TTL, or
       old enough that the archive now has the confirmed value).
    3. If the network/API fails, optionally fall back to deterministic *synthetic* climatology
       so the prototype keeps working offline (flagged via `used_synthetic`).

Coordinates are rounded to 2 decimals (~1.1 km) for the cache key; Open-Meteo's own grid is ~10 km.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import random
from dataclasses import dataclass, field
from typing import Iterable, Optional

import httpx
from sqlmodel import Session, select

from app.config import settings
from app.models.tables import DailyWeatherLog
from app.services.gdd_engine import compute_daily_gdd

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailyTemp:
    date: dt.date
    t_max: float
    t_min: float
    source: str  # archive | forecast | synthetic


@dataclass
class WeatherResult:
    temps: list[DailyTemp] = field(default_factory=list)
    used_synthetic: bool = False


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _daterange(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    for i in range((end - start).days + 1):
        yield start + dt.timedelta(days=i)


def synthetic_temperature(latitude: float, day: dt.date) -> DailyTemp:
    """Deterministic, plausible highland (~2,000 m, Atok/Buguias) daily temperatures.

    Mean ~15.5 C with a mild seasonal swing, +/-5.5 C diurnal half-range, and day-seeded noise.
    Used for offline development, seeding and tests -- never for real advisories.
    """
    doy = day.timetuple().tm_yday
    mean = 15.5 + 2.0 * math.sin(2 * math.pi * (doy - 60) / 365.25)   # warmest ~ end of May
    rng = random.Random(day.toordinal() * 1000 + int(round(latitude * 100)))
    shift = rng.gauss(0, 1.0)
    half_range = 5.5 + rng.gauss(0, 0.5)
    return DailyTemp(day, round(mean + shift + half_range, 1), round(mean + shift - half_range, 1), "synthetic")


class WeatherService:
    def __init__(self, session: Session, mode: Optional[str] = None, client: Optional[httpx.Client] = None):
        self.session = session
        self.mode = mode or settings.weather_mode
        self._client = client

    # ------------------------------------------------------------------ public
    def get_daily_temperatures(
        self,
        latitude: float,
        longitude: float,
        start: dt.date,
        end: dt.date,
        today: Optional[dt.date] = None,
    ) -> WeatherResult:
        """Daily T_max/T_min for [start, end], clipped to what exists (<= today + forecast horizon)."""
        today = today or settings.today()
        end = min(end, today + dt.timedelta(days=settings.forecast_horizon_days))
        if start > end:
            return WeatherResult()

        if self.mode == "synthetic":
            temps = [synthetic_temperature(latitude, d) for d in _daterange(start, end)]
            return WeatherResult(temps, used_synthetic=True)

        klat, klon = round(latitude, 2), round(longitude, 2)
        cached = self._load_cache(klat, klon, start, end)
        now = _utcnow()
        missing = [d for d in _daterange(start, end)
                   if d not in cached or self._is_stale(cached[d], today, now)]
        if missing:
            self._fetch_and_cache(klat, klon, missing, today)
            cached = self._load_cache(klat, klon, start, end)

        temps = {d: DailyTemp(d, r.t_max, r.t_min, r.source) for d, r in cached.items()}
        used_synthetic = False
        if settings.weather_fallback_synthetic:
            for d in _daterange(start, end):
                if d not in temps:
                    temps[d] = synthetic_temperature(latitude, d)
                    used_synthetic = True
            if used_synthetic:
                log.warning("Open-Meteo unavailable for (%.2f, %.2f); synthetic weather filled the gaps", klat, klon)
        return WeatherResult([temps[d] for d in sorted(temps)], used_synthetic)

    # ----------------------------------------------------------------- caching
    def _load_cache(self, lat: float, lon: float, start: dt.date, end: dt.date) -> dict[dt.date, DailyWeatherLog]:
        rows = self.session.exec(
            select(DailyWeatherLog).where(
                DailyWeatherLog.latitude == lat,
                DailyWeatherLog.longitude == lon,
                DailyWeatherLog.date >= start,
                DailyWeatherLog.date <= end,
            )
        ).all()
        return {r.date: r for r in rows}

    @staticmethod
    def _is_stale(row: DailyWeatherLog, today: dt.date, now: dt.datetime) -> bool:
        """Archive rows are final. Forecast rows expire, or get upgraded once the archive covers them."""
        if row.source != "forecast":
            return False
        archived_now = row.date <= today - dt.timedelta(days=settings.archive_lag_days)
        expired = now - row.fetched_at > dt.timedelta(hours=settings.weather_cache_ttl_hours)
        return archived_now or expired

    def _fetch_and_cache(self, lat: float, lon: float, missing: list[dt.date], today: dt.date) -> None:
        cutoff = today - dt.timedelta(days=settings.archive_lag_days)
        archive_days = [d for d in missing if d <= cutoff]
        forecast_days = [d for d in missing if d > cutoff]
        # Each segment is independent: if one fails we still keep the other.
        for days, url, source in (
            (archive_days, settings.open_meteo_archive_url, "archive"),
            (forecast_days, settings.open_meteo_forecast_url, "forecast"),
        ):
            if not days:
                continue
            try:
                rows = self._call_open_meteo(url, lat, lon, min(days), max(days))
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                log.warning("Open-Meteo %s request failed: %s", source, exc)
                continue
            self._upsert(lat, lon, rows, source)

    def _call_open_meteo(self, url: str, lat: float, lon: float, start: dt.date, end: dt.date):
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": "temperature_2m_max,temperature_2m_min",
            "timezone": settings.timezone,
        }
        client = self._client or httpx.Client(timeout=settings.http_timeout_s)
        try:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            daily = resp.json()["daily"]
        finally:
            if self._client is None:
                client.close()
        out = []
        for day_str, tmax, tmin in zip(daily["time"], daily["temperature_2m_max"], daily["temperature_2m_min"]):
            if tmax is None or tmin is None:   # Open-Meteo returns null for not-yet-available days
                continue
            out.append((dt.date.fromisoformat(day_str), float(tmax), float(tmin)))
        return out

    def _upsert(self, lat: float, lon: float, rows: list[tuple[dt.date, float, float]], source: str) -> None:
        if not rows:
            return
        existing = self._load_cache(lat, lon, min(r[0] for r in rows), max(r[0] for r in rows))
        now = _utcnow()
        for day, tmax, tmin in rows:
            gdd = round(compute_daily_gdd(tmax, tmin), 2)
            row = existing.get(day)
            if row:
                row.t_max, row.t_min, row.calculated_gdd = tmax, tmin, gdd
                row.source, row.fetched_at = source, now
                self.session.add(row)
            else:
                self.session.add(DailyWeatherLog(
                    date=day, latitude=lat, longitude=lon, t_max=tmax, t_min=tmin,
                    calculated_gdd=gdd, source=source, fetched_at=now))
        self.session.commit()
