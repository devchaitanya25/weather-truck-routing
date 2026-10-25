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
from django.core.cache import cache

from .errors import WeatherError

CHUNK_SIZE = 50
FORECAST_DAYS = 11  # covers the 10-day departure limit plus the 48 h heatmap
CACHE_SECONDS = 30 * 60  # Open-Meteo refreshes its models about hourly

Point = tuple[float, float]


def snap(lat: float, lon: float) -> Point:
    """Snap to the 0.1 degree grid used as the weather cache key."""
    return round(lat, 1), round(lon, 1)


def point_key(p: Point) -> str:
    """Stable string id for a snapped point; shared by plan, browser and evaluation."""
    return f"{p[0]:.1f},{p[1]:.1f}"


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


MAX_PAYLOAD_HOURS = 600


def forecast_from_payload(obj) -> Forecast:
    """Validate and convert hourly weather sent by the browser.

    Expected: {"start": epoch_s, "wind": [mph...], "rain": [in/hr...], "snow": [in/hr...]}
    with one value per hour starting at `start`.
    """
    from .errors import ValidationError

    try:
        start = float(obj["start"])
        wind, rain, snow = ([float(v) for v in obj[k]] for k in ("wind", "rain", "snow"))
    except (KeyError, TypeError, ValueError):
        raise ValidationError("Weather data is malformed.")
    n = len(wind)
    if not (2 <= n <= MAX_PAYLOAD_HOURS) or len(rain) != n or len(snow) != n:
        raise ValidationError("Weather data has an invalid length.")
    if not all(math.isfinite(v) and v >= 0 for v in wind + rain + snow) or not math.isfinite(start):
        raise ValidationError("Weather data contains invalid values.")
    return Forecast([start + i * 3600 for i in range(n)], wind, rain, snow)


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
    delays = (2, 5, 10)  # seconds to wait after a failed attempt (rate limits need real waits)
    for attempt in range(len(delays) + 1):
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
            if attempt < len(delays):
                time.sleep(delays[attempt])
    raise WeatherError(f"Weather service unavailable: {last_exc}")


def fetch_forecasts(
    points: list[Point], horizon_end_epoch: float, now_epoch: float | None = None
) -> dict[Point, Forecast]:
    """Fetch hourly forecasts for all points, covering up to horizon_end_epoch."""
    # Always request the full forecast window so cached entries are reusable by any trip.
    forecast_days = FORECAST_DAYS

    out: dict[Point, Forecast] = {}
    missing: list[Point] = []
    for p in points:
        cached = cache.get(f"wx:{p[0]}:{p[1]}")
        if cached is not None and cached.times[-1] >= horizon_end_epoch:
            out[p] = cached
        else:
            missing.append(p)

    if missing:
        # Few, sequential requests: the free tier limits requests per minute per IP.
        chunks = [missing[i : i + CHUNK_SIZE] for i in range(0, len(missing), CHUNK_SIZE)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda c: _fetch_chunk(c, forecast_days), chunks))
        for chunk, forecasts in zip(chunks, results):
            if len(forecasts) != len(chunk):
                raise WeatherError("Weather service returned an unexpected number of locations.")
            for p, fc in zip(chunk, forecasts):
                out[p] = fc
                cache.set(f"wx:{p[0]}:{p[1]}", fc, CACHE_SECONDS)
    return out
