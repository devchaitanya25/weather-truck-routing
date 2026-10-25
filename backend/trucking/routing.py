"""Route generation via OSRM (no API key needed).

OSRM's public demo only offers a car profile, so durations are scaled by
TRUCK_SPEED_FACTOR. Point OSRM_BASE_URL at your own OSRM (or swap this module
for OpenRouteService's driving-hgv profile) for real truck routing.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests
from django.conf import settings

from .errors import RoutingError
from .geo import Geometry, LatLon, bearing_deg, haversine_m, offset_point

WANTED_ROUTES = 3
MAX_VIA_ATTEMPTS = 4


@dataclass
class Route:
    id: str
    label: str
    via: str
    geometry: Geometry


def _geometry_from_osrm(route: dict, speed_factor: float) -> Geometry | None:
    """Stitch step geometries together, spreading each step's duration over its segments."""
    coords: list[LatLon] = []
    cum_m: list[float] = []
    cum_s: list[float] = []
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            pts = [(lat, lon) for lon, lat in step["geometry"]["coordinates"]]
            if len(pts) < 2:
                continue
            seg_lens = [haversine_m(a, b) for a, b in zip(pts, pts[1:])]
            total = sum(seg_lens) or 1.0
            duration = step["duration"] * speed_factor
            if not coords:
                coords.append(pts[0])
                cum_m.append(0.0)
                cum_s.append(0.0)
            for p, seg in zip(pts[1:], seg_lens):
                coords.append(p)
                cum_m.append(cum_m[-1] + seg)
                cum_s.append(cum_s[-1] + duration * seg / total)
    if len(coords) < 2 or cum_m[-1] <= 0:
        return None
    return Geometry(coords, cum_m, cum_s)


def _via_text(route: dict) -> str:
    names = [leg.get("summary", "") for leg in route.get("legs", []) if leg.get("summary")]
    return " + ".join(names)[:80] or "Route"


def _osrm(waypoints: list[LatLon], alternatives: bool) -> list[dict]:
    coord_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in waypoints)
    url = f"{settings.OSRM_BASE_URL}/route/v1/driving/{coord_str}"
    params = {"alternatives": "3" if alternatives else "false", "steps": "true",
              "geometries": "geojson", "overview": "false"}
    headers = {"User-Agent": settings.HTTP_USER_AGENT}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code == 400 and alternatives:  # older OSRM: boolean only
            params["alternatives"] = "true"
            resp = requests.get(url, params=params, headers=headers, timeout=30)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"Routing service unavailable: {exc}") from exc
    if data.get("code") == "NoRoute":
        raise RoutingError("No drivable route found between those locations.")
    if data.get("code") != "Ok":
        raise RoutingError(f"Routing service error: {data.get('message') or data.get('code')}")
    return data["routes"]


def _similar(a: Geometry, b: Geometry, tol_m: float = 1500.0) -> bool:
    fracs = [i / 10 for i in range(1, 10)]
    dists = [
        haversine_m(a.locate(a.total_m * f)[:2], b.locate(b.total_m * f)[:2]) for f in fracs
    ]
    return sum(dists) / len(dists) < tol_m


def get_routes(origin: LatLon, destination: LatLon) -> list[Route]:
    """Return up to three distinct routes, fastest first (labelled A, B, C)."""
    factor = settings.TRUCK_SPEED_FACTOR
    candidates: list[tuple[Geometry, str]] = []

    def add(raw: dict) -> None:
        geom = _geometry_from_osrm(raw, factor)
        if geom and not any(_similar(geom, g) for g, _ in candidates):
            candidates.append((geom, _via_text(raw)))

    for raw in _osrm([origin, destination], alternatives=True)[:WANTED_ROUTES]:
        add(raw)

    # OSRM often returns fewer than 3 alternatives. Force diversity by routing
    # through a waypoint pushed sideways from the midpoint of the main route.
    if candidates and len(candidates) < WANTED_ROUTES:
        base = candidates[0][0]
        mid = base.locate(base.total_m / 2)[:2]
        heading = bearing_deg(origin, destination)
        straight = haversine_m(origin, destination)
        attempts = 0
        for frac in (0.15, -0.15, 0.30, -0.30):
            if len(candidates) >= WANTED_ROUTES or attempts >= MAX_VIA_ATTEMPTS:
                break
            attempts += 1
            via = offset_point(mid, heading + 90, straight * frac)
            try:
                raws = _osrm([origin, via, destination], alternatives=False)
            except RoutingError:
                continue
            geom = _geometry_from_osrm(raws[0], factor)
            if geom is None or geom.total_m > base.total_m * 1.6:
                continue  # absurd detour
            add(raws[0])

    candidates.sort(key=lambda c: c[0].total_s)
    return [
        Route(id=f"route-{chr(97 + i)}", label=f"Route {chr(65 + i)}", via=via, geometry=geom)
        for i, (geom, via) in enumerate(candidates[:WANTED_ROUTES])
    ]
