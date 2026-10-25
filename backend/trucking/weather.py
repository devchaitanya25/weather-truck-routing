"""Hourly weather via Open-Meteo (no API key needed).

Forecasts are fetched in batches (many coordinates per HTTP request, several
requests in parallel) and cached per ~0.1 degree grid cell, which matches the
model resolution and lets overlapping routes share requests.
"""
from __future__ import annotations

import bisect
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from django.conf import settings

from .errors import WeatherError

CHUNK_SIZE = 50
MAX_FORECAST_DAYS = 16

Point = tuple[float, float]


def snap(lat: float, lon: float) -> Point:
    """Snap to the 0.1 degree grid used as the weather cache key."""
    return round(lat, 1), round(lon, 1)


@dataclass
class Sample:
    wind_mph: float
    rain_in_hr: float
    snow_in_hr: float
    beyond_forecast: bool = False


@dataclass
class Forecast:
    times: list[float]  # epoch seconds, ascending
    wind: list[float]
    rain: list[float]
    snow: list[float]

    def at(self, t: float) -> Sample:
        """Linearly interpolate hourly values at epoch time t (clamped to the data range)."""
        times = self.times
        if t <= times[0]:
            return Sample(self.wind[0], self.rain[0], self.snow[0])
        if t >= times[-1]:
            return Sample(self.wind[-1], self.rain[-1], self.snow[-1], beyond_forecast=t > times[-1])
        i = bisect.bisect_right(times, t) - 1
        f = (t - times[i]) / (times[i + 1] - times[i])

        def lerp(arr: list[float]) -> float:
            return arr[i] + f * (arr[i + 1] - arr[i])

        return Sample(lerp(self.wind), lerp(self.rain), lerp(self.snow))


def _parse_location(loc: dict) -> Forecast:
    h, units = loc["hourly"], loc.get("hourly_units", {})
    times = [
        datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() for s in h["time"]
    ]
    snow_scale = 1 / 2.54 if units.get("snowfall") == "cm" else 1.0

    def num(v):  # the API returns null for missing hours
        return v if v is not None else 0.0

    return Forecast(
        times=times,
        wind=[num(v) for v in h["wind_speed_10m"]],
        rain=[num(r) + num(s) for r, s in zip(h["rain"], h["showers"])],
        snow=[num(v) * snow_scale for v in h["snowfall"]],
    )


def _fetch_chunk(points: list[Point], forecast_days: int) -> list[Forecast]:
    params = {
        "latitude": ",".join(str(p[0]) for p in points),
        "longitude": ",".join(str(p[1]) for p in points),
        "hourly": "wind_speed_10m,rain,showers,snowfall",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "UTC",
        "past_days": 1,
        "forecast_days": forecast_days,
    }
    headers = {"User-Agent": settings.HTTP_USER_AGENT}
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.get(settings.OPEN_METEO_URL, params=params, headers=headers, timeout=30)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise WeatherError(f"Weather service returned HTTP {resp.status_code}")
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                raise WeatherError(f"Weather service error: {data.get('reason')}")
            locations = data if isinstance(data, list) else [data]
            return [_parse_location(loc) for loc in locations]
        except (requests.RequestException, ValueError, KeyError, WeatherError) as exc:
            last_exc = exc
            time.sleep(0.6 * (attempt + 1))
    raise WeatherError(f"Weather service unavailable: {last_exc}")


def fetch_forecasts(
    points: list[Point], horizon_end_epoch: float, now_epoch: float | None = None
) -> dict[Point, Forecast]:
    """Fetch hourly forecasts for all points, covering up to horizon_end_epoch."""
    now = now_epoch if now_epoch is not None else time.time()
    today_start = now - (now % 86400)
    days = math.ceil((horizon_end_epoch - today_start) / 86400)
    forecast_days = min(MAX_FORECAST_DAYS, max(2, days))

    chunks = [points[i : i + CHUNK_SIZE] for i in range(0, len(points), CHUNK_SIZE)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda c: _fetch_chunk(c, forecast_days), chunks))

    out: dict[Point, Forecast] = {}
    for chunk, forecasts in zip(chunks, results):
        if len(forecasts) != len(chunk):
            raise WeatherError("Weather service returned an unexpected number of locations.")
        out.update(zip(chunk, forecasts))
    return out
