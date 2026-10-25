"""Place search via Photon (OpenStreetMap data, typeahead-friendly, no key)."""
from __future__ import annotations

import requests
from django.conf import settings

from .errors import ExternalServiceError


def _label(props: dict) -> str:
    parts: list[str] = []
    for key in ("name", "street", "city", "state", "country"):
        v = props.get(key)
        if v and v not in parts:
            parts.append(v)
    return ", ".join(parts)


def search(query: str, limit: int = 6) -> list[dict]:
    try:
        resp = requests.get(
            settings.PHOTON_URL,
            params={"q": query, "limit": limit, "lang": "en"},
            headers={"User-Agent": settings.HTTP_USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except (requests.RequestException, ValueError) as exc:
        raise ExternalServiceError(f"Geocoding service unavailable: {exc}") from exc

    results = []
    for f in features:
        lon, lat = f["geometry"]["coordinates"]
        label = _label(f.get("properties", {}))
        if label:
            results.append({"label": label, "lat": lat, "lng": lon})
    return results
