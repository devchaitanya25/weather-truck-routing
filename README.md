# Weather-Aware Truck Routing

React + Django app that compares three routes for a truck, samples weather at the truck's *estimated arrival time* at each checkpoint, scores risk using the assessment's rules (including load-weight rules), recommends a route, and shows a 0–48 h weather heatmap along the corridor.

**No API keys required.** Routing: OSRM · Weather: Open-Meteo · Geocoding: Photon (OpenStreetMap).

## Run locally

```bash
# Backend
cd backend
pip install -r requirements.txt
python manage.py test          # unit tests
python manage.py runserver     # http://127.0.0.1:8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev                    # http://localhost:5173 (proxies /api to Django)
```

## Deploy (single container)

The `Dockerfile` builds the React app and serves it from Django (WhiteNoise), so one service hosts everything.

## How it works

An analysis is three steps:

1. `POST /api/plan/` → `analysis.build_plan()`
   - **Routes** (`routing.py`): OSRM alternatives; if fewer than 3 distinct routes come back, extra ones are forced via sideways waypoints. Durations are scaled ×1.15 for truck speeds (the OSRM public demo only has a car profile).
   - **Checkpoints**: every 10/25/50 miles plus the destination. ETA = departure + cumulative route time at that distance.
   - Returns the routes/checkpoints plus the list of weather points needed (checkpoints + a sampled heatmap corridor, snapped to a 0.1° grid).
2. **Browser fetches weather** (`frontend/src/api.js`): hourly wind/rain/snow from Open-Meteo, in mph and inches, batched 50 points per request, with retry/backoff on 429 and a 30-minute in-memory cache. *Why in the browser:* Open-Meteo's free tier rate-limits by IP, and shared hosting IPs (e.g. Render's free tier) get HTTP 429. Fetching from each visitor's own IP avoids that.
3. `POST /api/analyze/` with `{plan, weather}` → `analysis.evaluate()` — **all scoring stays in Django**:
   - **Weather** (`weather.py`): payload validated, linearly interpolated to each checkpoint's ETA.
   - **Risk** (`risk.py`): table thresholds + load rules; the worst of wind/rain/snow sets the level.
   - **Ranking** (`recommend.py`): No Travel miles → Severe miles → High miles → average risk → travel time.
   - **Heatmap**: ≤60 sample points along all routes × 49 hourly levels, sent once so the slider is instant (no requests while dragging).

If `plan`/`weather` are omitted, `/api/analyze/` falls back to fetching weather server-side (`analysis.analyze()`), which works fine locally or on an unthrottled host.

## Assumptions / judgment calls

- The risk table shares edge values (e.g. rain 0.25 is in both Moderate and High). Resolved as: Moderate is `0.10–0.25` inclusive, High starts above 0.25; same for other bands. Wind bands are lower-bound inclusive (35 mph = High).
- Each checkpoint represents the stretch halfway to its neighbours; "miles" per level and average risk are weighted by that stretch.
- **Added tie-break:** routes with *No Travel* miles are not drivable, so fewest No Travel miles is compared first. Comparisons round to the nearest mile (0.1 for average risk) so negligible differences don't outrank a much faster route.
- Rain = Open-Meteo `rain + showers`; snow converted from cm to inches. Forecast limited to 10 days ahead.
- Weather is cached per 0.1° grid cell (model resolution); ETAs past the forecast use the last forecast hour and the UI shows a note.

## Known limitations

- OSRM public demo is for light use and has no truck profile (no height/weight restrictions). Set `OSRM_BASE_URL` to your own server or swap in OpenRouteService `driving-hgv`.
- Heatmap is sampled (~25 mi spacing), not a full grid.
- The plan and weather are sent back to the server by the client (stateless server, no database). The server validates shapes but trusts the numbers, which is fine for a planning tool but would need signing for anything adversarial.
- Weather is pulled live per analysis (cached 30 min in the browser); geocoding results are cached on the server.

## Layout

```
backend/trucking/   risk.py  routing.py  weather.py  recommend.py  analysis.py  views.py  tests.py
frontend/src/       App.jsx  MapView.jsx  PlaceInput.jsx  api.js
```
