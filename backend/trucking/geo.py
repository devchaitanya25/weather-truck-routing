"""Small geodesy + polyline helpers."""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_008.8
M_PER_MILE = 1609.344

LatLon = tuple[float, float]


def haversine_m(a: LatLon, b: LatLon) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a: LatLon, b: LatLon) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def offset_point(origin: LatLon, bearing: float, dist_m: float) -> LatLon:
    lat1, lon1 = math.radians(origin[0]), math.radians(origin[1])
    ang, brg = dist_m / EARTH_RADIUS_M, math.radians(bearing)
    lat2 = math.asin(math.sin(lat1) * math.cos(ang) + math.cos(lat1) * math.sin(ang) * math.cos(brg))
    lon2 = lon1 + math.atan2(
        math.sin(brg) * math.sin(ang) * math.cos(lat1),
        math.cos(ang) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180


@dataclass
class Geometry:
    """A route polyline with cumulative distance (m) and travel time (s) per vertex."""

    coords: list[LatLon]
    cum_m: list[float]
    cum_s: list[float]

    @classmethod
    def from_points(cls, coords: list[LatLon], total_seconds: float) -> "Geometry":
        """Build a geometry whose time is proportional to distance (used by tests)."""
        cum_m = [0.0]
        for a, b in zip(coords, coords[1:]):
            cum_m.append(cum_m[-1] + haversine_m(a, b))
        total = cum_m[-1] or 1.0
        return cls(list(coords), cum_m, [total_seconds * d / total for d in cum_m])

    @property
    def total_m(self) -> float:
        return self.cum_m[-1]

    @property
    def total_s(self) -> float:
        return self.cum_s[-1]

    def locate(self, d_m: float) -> tuple[float, float, float]:
        """Return (lat, lon, seconds_from_start) at distance d_m along the route."""
        d = min(max(d_m, 0.0), self.total_m)
        i = min(bisect.bisect_right(self.cum_m, d) - 1, len(self.coords) - 2)
        i = max(i, 0)
        seg = self.cum_m[i + 1] - self.cum_m[i]
        f = (d - self.cum_m[i]) / seg if seg > 0 else 0.0
        (la1, lo1), (la2, lo2) = self.coords[i], self.coords[i + 1]
        secs = self.cum_s[i] + f * (self.cum_s[i + 1] - self.cum_s[i])
        return la1 + f * (la2 - la1), lo1 + f * (lo2 - lo1), secs

    def slice(self, start_m: float, end_m: float, max_points: int = 60) -> list[list[float]]:
        """Polyline between two distances, thinned to roughly max_points vertices."""
        lo = bisect.bisect_right(self.cum_m, start_m)
        hi = bisect.bisect_left(self.cum_m, end_m)
        interior = self.coords[lo:hi]
        stride = max(1, math.ceil(len(interior) / max_points))
        s_lat, s_lon, _ = self.locate(start_m)
        e_lat, e_lon, _ = self.locate(end_m)
        pts = [[s_lat, s_lon]] + [[la, lo_] for la, lo_ in interior[::stride]] + [[e_lat, e_lon]]
        return pts
