"""One analysis, in two steps so weather can come from the browser.

build_plan()  - routes, checkpoints (with ETAs), and the list of weather points needed.
evaluate()    - given forecasts for those points, scores risk, ranks routes, builds the heatmap.

analyze() runs both with server-side weather (used by tests and local use).
In production the browser fetches the weather itself (see frontend/src/api.js), because
Open-Meteo's free tier rate-limits shared hosting IPs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from . import recommend, routing, weather
from .errors import ValidationError
from .geo import M_PER_MILE
from .risk import LEVEL_NAMES, classify

HEAT_HOURS = 48
HEAT_MAX_POINTS = 60
HEAT_SPACING_MI = 40


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


# ----------------------------------------------------------------------------- step 1


def build_plan(req: AnalysisRequest, *, get_routes=routing.get_routes) -> dict:
    """Everything that does not depend on weather. The result is plain JSON."""
    routes = get_routes((req.origin.lat, req.origin.lng), (req.destination.lat, req.destination.lng))
    if not routes or routes[0].geometry.total_m < 0.5 * M_PER_MILE:
        raise ValidationError("Origin and destination are too close together to route.")

    dep = req.departure.timestamp()
    horizon = dep + HEAT_HOURS * 3600
    needed: dict[str, weather.Point] = {}

    def register(lat: float, lon: float) -> str:
        p = weather.snap(lat, lon)
        key = weather.point_key(p)
        needed[key] = p
        return key

    plan_routes = []
    for r in routes:
        g = r.geometry
        ds = checkpoint_distances(g.total_m, req.interval_mi)
        cps = []
        for i, d in enumerate(ds):
            lat, lon, secs = g.locate(d)
            start = (ds[i - 1] + d) / 2 if i > 0 else 0.0
            end = (d + ds[i + 1]) / 2 if i < len(ds) - 1 else g.total_m
            eta = dep + secs
            horizon = max(horizon, eta)
            span_mi = (end - start) / M_PER_MILE
            max_pts = int(min(60, max(8, round(span_mi * 1.2))))
            cps.append({
                "mile_m": d,
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "eta": eta,
                "start_m": start,
                "end_m": end,
                "key": register(lat, lon),
                "path": [[round(a, 5), round(b, 5)] for a, b in g.slice(start, end, max_pts)],
            })
        plan_routes.append({
            "id": r.id, "label": r.label, "via": r.via,
            "total_m": g.total_m, "total_s": g.total_s, "checkpoints": cps,
        })

    heat_keys = [register(*p) for p in _heat_points(routes)]

    return {
        "request": {
            "origin": req.origin.__dict__,
            "destination": req.destination.__dict__,
            "departure": _iso(dep),
            "departure_epoch": dep,
            "load_lb": req.load_lb,
            "interval_mi": req.interval_mi,
        },
        "routes": plan_routes,
        "heat_keys": heat_keys,
        # Hourly weather is needed for every point between window.start and window.end (epoch s).
        "window": {
            "start": int(dep // 3600 * 3600) - 3600,
            "end": int(math.ceil(horizon / 3600) * 3600) + 3600,
        },
        "weather_points": [
            {"key": k, "lat": p[0], "lon": p[1]} for k, p in sorted(needed.items())
        ],
    }


# ----------------------------------------------------------------------------- step 2


def _forecast(forecasts: dict[str, weather.Forecast], key: str) -> weather.Forecast:
    try:
        return forecasts[key]
    except KeyError:
        raise ValidationError(f"Missing weather data for point {key}.")


def _build_route(r: dict, forecasts, load_lb: float, dep: float, notes: set[str]) -> dict:
    total_m, total_s = float(r["total_m"]), float(r["total_s"])
    miles_by_level = [0.0] * 5
    weighted = 0.0
    checkpoints, segments = [], []

    for i, cp in enumerate(r["checkpoints"]):
        sample = _forecast(forecasts, cp["key"]).at(cp["eta"])
        if sample.beyond_forecast:
            notes.add("Some ETAs fall beyond the available forecast; the last forecast hour was used.")
        a = classify(sample.wind_mph, sample.rain_in_hr, sample.snow_in_hr, load_lb)
        span_m = cp["end_m"] - cp["start_m"]
        miles_by_level[a.level] += span_m / M_PER_MILE
        weighted += a.level * span_m

        checkpoints.append({
            "index": i,
            "mile": round(cp["mile_m"] / M_PER_MILE, 1),
            "lat": cp["lat"],
            "lon": cp["lon"],
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
        path = [list(p) for p in cp["path"]]
        if segments and segments[-1]["level"] == a.level:
            segments[-1]["path"].extend(path[1:])
        else:
            segments.append({"level": a.level, "path": path})

    named = {LEVEL_NAMES[i]: round(miles_by_level[i], 1) for i in range(5)}
    return {
        "id": r["id"],
        "label": r["label"],
        "via": r["via"],
        "distance_mi": round(total_m / M_PER_MILE, 1),
        "duration_s": round(total_s),
        "departure": _iso(dep),
        "arrival": _iso(dep + total_s),
        "miles_by_level": named,
        "avg_risk": round(weighted / total_m if total_m else 0.0, 3),
        "max_level": max(c["level"] for c in checkpoints),
        "drivable": named["No Travel"] < 0.5,
        "checkpoints": checkpoints,
        "segments": segments,
    }


def _build_heatmap(plan: dict, forecasts, load_lb: float, dep: float) -> dict:
    coords = {p["key"]: (p["lat"], p["lon"]) for p in plan["weather_points"]}
    rows = []
    for key in plan["heat_keys"]:
        fc = _forecast(forecasts, key)
        levels = []
        for h in range(HEAT_HOURS + 1):
            s = fc.at(dep + h * 3600)
            levels.append(classify(s.wind_mph, s.rain_in_hr, s.snow_in_hr, load_lb).level)
        lat, lon = coords[key]
        rows.append([lat, lon, levels])
    return {"start": _iso(dep), "hours": HEAT_HOURS, "points": rows}


def evaluate(plan: dict, forecasts: dict[str, weather.Forecast]) -> dict:
    """Score a plan against forecasts keyed by point key."""
    try:
        req = plan["request"]
        load_lb = float(req["load_lb"])
        dep = float(req["departure_epoch"])
        notes: set[str] = set()
        results = [_build_route(r, forecasts, load_lb, dep, notes) for r in plan["routes"]]
        if not results:
            raise ValidationError("Plan contains no routes.")
        heatmap = _build_heatmap(plan, forecasts, load_lb, dep)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise ValidationError(f"Invalid plan data: {exc!r}")

    ranked = recommend.rank_routes(results)
    return {
        "request": {k: req[k] for k in ("origin", "destination", "departure", "load_lb", "interval_mi")},
        "routes": results,  # original (fastest-first) order; `rank` marks the best
        "recommendation": recommend.explain(ranked),
        "heatmap": heatmap,
        "notes": sorted(notes),
    }


def analyze(
    req: AnalysisRequest,
    *,
    get_routes=routing.get_routes,
    fetch_forecasts=weather.fetch_forecasts,
) -> dict:
    """Plan + server-side weather + evaluate (tests, local use)."""
    plan = build_plan(req, get_routes=get_routes)
    points = [(p["lat"], p["lon"]) for p in plan["weather_points"]]
    fetched = fetch_forecasts(points, plan["window"]["end"])
    forecasts = {weather.point_key(p): fc for p, fc in fetched.items()}
    return evaluate(plan, forecasts)
