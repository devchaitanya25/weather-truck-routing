from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from . import geocoding
from .analysis import AnalysisRequest, Place, analyze
from .errors import ApiError, ValidationError

ALLOWED_INTERVALS = (10, 25, 50)
MAX_LOAD_LB = 150_000
MAX_DAYS_AHEAD = 10


def _place(obj, field: str) -> Place:
    try:
        lat, lng = float(obj["lat"]), float(obj["lng"])
    except (TypeError, KeyError, ValueError):
        raise ValidationError(f"'{field}' needs numeric lat and lng.")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValidationError(f"'{field}' coordinates are out of range.")
    return Place(lat, lng, str(obj.get("label", ""))[:200])


def parse_request(body: dict, now: datetime | None = None) -> AnalysisRequest:
    now = now or datetime.now(timezone.utc)
    origin = _place(body.get("origin"), "origin")
    destination = _place(body.get("destination"), "destination")

    try:
        departure = datetime.fromisoformat(str(body["departure"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        raise ValidationError("'departure' must be an ISO 8601 date/time.")
    if departure.tzinfo is None:
        departure = departure.replace(tzinfo=timezone.utc)
    if departure < now - timedelta(hours=1):
        raise ValidationError("Departure time is in the past.")
    if departure > now + timedelta(days=MAX_DAYS_AHEAD):
        raise ValidationError(f"Departure must be within {MAX_DAYS_AHEAD} days (forecast horizon).")

    try:
        load_lb = float(body["load_lb"])
    except (KeyError, TypeError, ValueError):
        raise ValidationError("'load_lb' must be a number.")
    if not (0 <= load_lb <= MAX_LOAD_LB):
        raise ValidationError(f"'load_lb' must be between 0 and {MAX_LOAD_LB:,}.")

    try:
        interval = int(body.get("interval_mi", 25))
    except (TypeError, ValueError):
        interval = 0
    if interval not in ALLOWED_INTERVALS:
        raise ValidationError(f"'interval_mi' must be one of {ALLOWED_INTERVALS}.")

    return AnalysisRequest(origin, destination, departure, load_lb, interval)


def _error(exc: ApiError) -> JsonResponse:
    return JsonResponse({"error": str(exc)}, status=exc.status)


@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


@require_GET
def geocode(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 3:
        return JsonResponse({"results": []})
    key = f"geocode:{q.lower()}"
    results = cache.get(key)
    if results is None:
        try:
            results = geocoding.search(q)
        except ApiError as exc:
            return _error(exc)
        cache.set(key, results, 3600)
    return JsonResponse({"results": results})


@require_POST
def analyze_view(request):
    t0 = time.perf_counter()
    try:
        try:
            body = json.loads(request.body or b"{}")
        except ValueError:
            raise ValidationError("Request body must be valid JSON.")
        result = analyze(parse_request(body))
    except ApiError as exc:
        return _error(exc)
    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
    return JsonResponse(result)
