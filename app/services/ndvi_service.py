"""OPTIONAL Earth Observation module: Sentinel-2 NDVI time series via Google Earth Engine.

Deliberately decoupled: nothing in the core system imports `earthengine-api`. Callers ask for
`get_ndvi_provider()` and get either the real GEE provider or a harmless Null provider, so
AgroCast runs identically with or without GEE credentials.

Intended future uses (not wired into the core yet):
    * Verify a farmer-reported planting date (NDVI rises from bare-soil ~0.2 as the canopy closes)
    * Detect harvest events (sharp NDVI drop) to flip a parcel to `harvested` automatically

Setup:  pip install earthengine-api ; earthengine authenticate ;
        export AGROCAST_GEE_ENABLED=true AGROCAST_GEE_PROJECT=<your-gcp-project>
NOTE: this file could not be exercised without GEE credentials in the dev sandbox -- test it once locally.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Protocol

from app.config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NDVIObservation:
    date: dt.date
    ndvi: float


class NDVIProvider(Protocol):
    def is_available(self) -> bool: ...
    def get_ndvi_series(self, latitude: float, longitude: float,
                        start: dt.date, end: dt.date) -> list[NDVIObservation]: ...


class NullNDVIProvider:
    """Used when GEE is disabled/unavailable. Returns no observations."""
    def is_available(self) -> bool:
        return False

    def get_ndvi_series(self, latitude, longitude, start, end) -> list[NDVIObservation]:
        return []


class GEENDVIProvider:
    """Sentinel-2 SR harmonized, NDVI = (B8 - B4) / (B8 + B4), mean within a 30 m buffer."""

    def __init__(self) -> None:
        self._ee = None
        self._ready = False

    def is_available(self) -> bool:
        if self._ready:
            return True
        try:
            import ee  # lazy import: optional dependency
            ee.Initialize(project=settings.gee_project or None)
            self._ee, self._ready = ee, True
        except Exception as exc:  # ImportError, auth errors, network errors ...
            log.warning("Google Earth Engine unavailable: %s", exc)
        return self._ready

    def get_ndvi_series(self, latitude, longitude, start, end) -> list[NDVIObservation]:
        if not self.is_available():
            return []
        ee = self._ee
        point = ee.Geometry.Point([longitude, latitude])
        region = point.buffer(30)
        collection = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(point)
            .filterDate(start.isoformat(), (end + dt.timedelta(days=1)).isoformat())
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
        )

        def per_image(img):
            ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
            mean = ndvi.reduceRegion(ee.Reducer.mean(), region, 10).get("NDVI")
            return ee.Feature(None, {"date": img.date().format("YYYY-MM-dd"), "ndvi": mean})

        features = collection.map(per_image).filter(ee.Filter.notNull(["ndvi"])).getInfo()["features"]
        series = [NDVIObservation(dt.date.fromisoformat(f["properties"]["date"]), float(f["properties"]["ndvi"]))
                  for f in features]
        return sorted(series, key=lambda o: o.date)


def get_ndvi_provider() -> NDVIProvider:
    """Factory: real provider only if enabled AND initialisable, otherwise the Null provider."""
    if settings.gee_enabled:
        provider = GEENDVIProvider()
        if provider.is_available():
            return provider
    return NullNDVIProvider()
