from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from . import geocoding
from .analysis import AnalysisRequest, Place, analyze, build_plan, evaluate
from .errors import ApiError, ValidationError
from .weather import forecast_from_payload

MAX_WEATHER_POINTS = 800
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


def _json_body(request) -> dict:
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        raise ValidationError("Request body must be valid JSON.")
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object.")
    return body


@require_POST
def plan_view(request):
    """Step 1: routes, checkpoints, ETAs and the weather points the browser must fetch."""
    try:
        return JsonResponse(build_plan(parse_request(_json_body(request))))
    except ApiError as exc:
        return _error(exc)


@require_POST
def analyze_view(request):
    """Step 2: score a plan against weather supplied by the browser.

    Without `plan`/`weather` in the body, falls back to fetching weather server-side.
    """
    t0 = time.perf_counter()
    try:
        body = _json_body(request)
        if "plan" in body:
            weather_in = body.get("weather")
            if not isinstance(weather_in, dict) or len(weather_in) > MAX_WEATHER_POINTS:
                raise ValidationError("'weather' must be an object keyed by point.")
            forecasts = {k: forecast_from_payload(v) for k, v in weather_in.items()}
            plan = body["plan"]
            if not isinstance(plan, dict):
                raise ValidationError("'plan' must be an object.")
            result = evaluate(plan, forecasts)
        else:
            result = analyze(parse_request(body))
    except ApiError as exc:
        return _error(exc)
    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
    return JsonResponse(result)
