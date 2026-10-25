"""Orchestrates one analysis: routes -> checkpoints -> weather -> risk -> ranking -> heatmap."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from . import recommend, routing, weather
from .errors import ValidationError
from .geo import M_PER_MILE, Geometry
from .risk import LEVEL_NAMES, classify

HEAT_HOURS = 48
HEAT_MAX_POINTS = 60
HEAT_SPACING_MI = 40
SEGMENT_MAX_POINTS = 60


@dataclass(frozen=True)
class Place:
    lat: float
    lng: float
    label: str = ""


@dataclass(frozen=True)
class AnalysisRequest:
    origin: Place
    destination: Place
    departure: datetime
    load_lb: float
    interval_mi: int


def checkpoint_distances(total_m: float, interval_mi: float) -> list[float]:
    """Distances (m) of checkpoints: start, every interval, and the destination."""
    step = interval_mi * M_PER_MILE
    ds = [k * step for k in range(int(total_m // step) + 1)]
    if total_m - ds[-1] > M_PER_MILE:
        ds.append(total_m)
    else:
        ds[-1] = total_m
    return ds


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _heat_points(routes: list[routing.Route]) -> list[weather.Point]:
    pts: set[weather.Point] = set()
    for r in routes:
        total = r.geometry.total_m
        step = max(HEAT_SPACING_MI * M_PER_MILE, total / 25)
        for k in range(int(total // step) + 2):
            lat, lon, _ = r.geometry.locate(min(total, k * step))
            pts.add(weather.snap(lat, lon))
    ordered = sorted(pts)
    if len(ordered) > HEAT_MAX_POINTS:
        stride = math.ceil(len(ordered) / HEAT_MAX_POINTS)
        ordered = ordered[::stride]
    return ordered


def _plan(route: routing.Route, interval_mi: int, dep: float) -> list[dict]:
    g: Geometry = route.geometry
    ds = checkpoint_distances(g.total_m, interval_mi)
    cps = []
    for i, d in enumerate(ds):
        lat, lon, secs = g.locate(d)
        start = (ds[i - 1] + d) / 2 if i > 0 else 0.0
        end = (d + ds[i + 1]) / 2 if i < len(ds) - 1 else g.total_m
        cps.append({"d": d, "lat": lat, "lon": lon, "eta": dep + secs, "start": start, "end": end})
    return cps


def _build_route(route, cps, forecasts, load_lb, dep, notes: set[str]) -> dict:
    g = route.geometry
    miles_by_level = [0.0] * 5
    weighted = 0.0
    checkpoints, segments = [], []

    for i, cp in enumerate(cps):
        sample = forecasts[weather.snap(cp["lat"], cp["lon"])].at(cp["eta"])
        if sample.beyond_forecast:
            notes.add("Some ETAs fall beyond the available forecast; the last forecast hour was used.")
        a = classify(sample.wind_mph, sample.rain_in_hr, sample.snow_in_hr, load_lb)
        span_m = cp["end"] - cp["start"]
        miles_by_level[a.level] += span_m / M_PER_MILE
        weighted += a.level * span_m

        checkpoints.append({
            "index": i,
            "mile": round(cp["d"] / M_PER_MILE, 1),
            "lat": round(cp["lat"], 5),
            "lon": round(cp["lon"], 5),
            "eta": _iso(cp["eta"]),
            "weather": {
                "wind_mph": round(sample.wind_mph, 1),
                "rain_in_hr": round(sample.rain_in_hr, 3),
                "snow_in_hr": round(sample.snow_in_hr, 3),
            },
            "level": a.level,
            "level_name": a.level_name,
            "driver": a.driver,
            "reason": a.reason,
        })

        # Merge consecutive spans of the same level into one colored segment.
        if segments and segments[-1]["level"] == a.level:
            segments[-1]["end_m"] = cp["end"]
        else:
            segments.append({"level": a.level, "start_m": cp["start"], "end_m": cp["end"]})

    for seg in segments:
        seg["path"] = g.slice(seg.pop("start_m"), seg.pop("end_m"), SEGMENT_MAX_POINTS)

    total_m = g.total_m
    avg_risk = weighted / total_m if total_m else 0.0
    named = {LEVEL_NAMES[i]: round(miles_by_level[i], 1) for i in range(5)}
    return {
        "id": route.id,
        "label": route.label,
        "via": route.via,
        "distance_mi": round(total_m / M_PER_MILE, 1),
        "duration_s": round(g.total_s),
        "departure": _iso(dep),
        "arrival": _iso(dep + g.total_s),
        "miles_by_level": named,
        "avg_risk": round(avg_risk, 3),
        "max_level": max(c["level"] for c in checkpoints),
        "drivable": named["No Travel"] < 0.5,
        "checkpoints": checkpoints,
        "segments": segments,
    }


def _build_heatmap(points, forecasts, load_lb, dep) -> dict:
    rows = []
    for p in points:
        fc = forecasts[p]
        levels = []
        for h in range(HEAT_HOURS + 1):
            s = fc.at(dep + h * 3600)
            levels.append(classify(s.wind_mph, s.rain_in_hr, s.snow_in_hr, load_lb).level)
        rows.append([p[0], p[1], levels])
    return {"start": _iso(dep), "hours": HEAT_HOURS, "points": rows}


def analyze(
    req: AnalysisRequest,
    *,
    get_routes=routing.get_routes,
    fetch_forecasts=weather.fetch_forecasts,
) -> dict:
    routes = get_routes((req.origin.lat, req.origin.lng), (req.destination.lat, req.destination.lng))
    if not routes or routes[0].geometry.total_m < 0.5 * M_PER_MILE:
        raise ValidationError("Origin and destination are too close together to route.")

    dep = req.departure.timestamp()
    plans = {r.id: _plan(r, req.interval_mi, dep) for r in routes}
    heat_pts = _heat_points(routes)

    needed = set(heat_pts)
    horizon = dep + HEAT_HOURS * 3600
    for cps in plans.values():
        for cp in cps:
            needed.add(weather.snap(cp["lat"], cp["lon"]))
            horizon = max(horizon, cp["eta"])
    forecasts = fetch_forecasts(sorted(needed), horizon)

    notes: set[str] = set()
    results = [
        _build_route(r, plans[r.id], forecasts, req.load_lb, dep, notes) for r in routes
    ]
    ranked = recommend.rank_routes(results)
    recommendation = recommend.explain(ranked)

    return {
        "request": {
            "origin": req.origin.__dict__,
            "destination": req.destination.__dict__,
            "departure": _iso(dep),
            "load_lb": req.load_lb,
            "interval_mi": req.interval_mi,
        },
        "routes": results,  # original (fastest-first) order; `rank` marks the best
        "recommendation": recommendation,
        "heatmap": _build_heatmap(heat_pts, forecasts, req.load_lb, dep),
        "notes": sorted(notes),
    }
