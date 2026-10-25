from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from . import recommend, routing, weather
from .analysis import AnalysisRequest, Place, analyze, build_plan, checkpoint_distances, evaluate
from .errors import ValidationError
from .geo import M_PER_MILE, Geometry
from .risk import HIGH, LOW, MODERATE, NO_TRAVEL, SEVERE, classify
from .views import parse_request


class RiskTests(SimpleTestCase):
    def test_wind_thresholds(self):
        for mph, lvl in [(10, LOW), (25, MODERATE), (34.9, MODERATE), (35, HIGH), (44, HIGH),
                         (45, SEVERE), (54, SEVERE), (55, NO_TRAVEL), (80, NO_TRAVEL)]:
            self.assertEqual(classify(mph, 0, 0, 10_000).level, lvl, mph)

    def test_rain_thresholds(self):
        for r, lvl in [(0.05, LOW), (0.10, MODERATE), (0.25, MODERATE), (0.3, HIGH),
                       (0.5, HIGH), (0.7, SEVERE), (1.0, SEVERE), (1.2, NO_TRAVEL)]:
            self.assertEqual(classify(0, r, 0, 10_000).level, lvl, r)

    def test_snow_thresholds(self):
        for s, lvl in [(0.4, LOW), (0.5, MODERATE), (1.0, MODERATE), (1.5, HIGH),
                       (2.0, HIGH), (2.5, SEVERE), (3.0, SEVERE), (3.5, NO_TRAVEL)]:
            self.assertEqual(classify(0, 0, s, 10_000).level, lvl, s)

    def test_load_rules(self):
        # 55+ is No Travel for any load
        self.assertEqual(classify(55, 0, 0, 0).level, NO_TRAVEL)
        # 45-54 with >30k -> No Travel, but not at exactly 30k
        self.assertEqual(classify(50, 0, 0, 30_001).level, NO_TRAVEL)
        self.assertEqual(classify(50, 0, 0, 30_000).level, SEVERE)
        # 35-44 with >40k -> Severe, but not at exactly 40k
        self.assertEqual(classify(40, 0, 0, 40_001).level, SEVERE)
        self.assertEqual(classify(40, 0, 0, 40_000).level, HIGH)
        # light wind never escalated by load
        self.assertEqual(classify(20, 0, 0, 80_000).level, LOW)

    def test_worst_factor_wins(self):
        a = classify(10, 0.7, 0.6, 10_000)
        self.assertEqual((a.level, a.driver), (SEVERE, "rain"))


def _route(rid, label, coords, secs):
    return routing.Route(rid, label, "test", Geometry.from_points(coords, secs))


# ~100 mile straight line heading east
LINE = [(40.0, -100.0), (40.0, -98.5)]


def _fake_forecast(wind=0.0, rain=0.0, snow=0.0, start=0.0, hours=100):
    t = [start + i * 3600 for i in range(hours)]
    return weather.Forecast(t, [wind] * hours, [rain] * hours, [snow] * hours)


class AnalysisTests(SimpleTestCase):
    def setUp(self):
        self.dep = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        self.req = AnalysisRequest(Place(40, -100), Place(40, -98.5), self.dep, 35_000, 25)

    def test_checkpoint_distances(self):
        ds = checkpoint_distances(100 * M_PER_MILE, 25)
        self.assertEqual([round(d / M_PER_MILE) for d in ds], [0, 25, 50, 75, 100])
        ds = checkpoint_distances(60 * M_PER_MILE, 25)
        self.assertEqual([round(d / M_PER_MILE) for d in ds], [0, 25, 50, 60])

    def test_eta_and_recommendation(self):
        calm = _route("route-a", "Route A", LINE, 7200)
        windy = _route("route-b", "Route B", LINE, 3600)  # faster but windy

        def fake_routes(o, d):
            return [windy, calm]

        def fake_fc(points, horizon):
            # windy region is simulated through the load: use 50 mph for lat>=40.0 in all cells,
            # so differentiate routes via time instead: wind only during first 90 minutes
            start = self.dep.timestamp()
            t = [start + i * 3600 for i in range(100)]
            wind = [50.0, 50.0] + [5.0] * 98
            z = [0.0] * 100
            return {p: weather.Forecast(t, wind, z, z) for p in points}

        result = analyze(self.req, get_routes=fake_routes, fetch_forecasts=fake_fc)
        by_id = {r["id"]: r for r in result["routes"]}
        # fast route is inside the storm for ~1h of a 1h trip; slow route exits it sooner (relative)
        self.assertEqual(len(result["routes"]), 2)
        self.assertGreater(by_id["route-b"]["miles_by_level"]["No Travel"], 0)
        self.assertEqual(result["recommendation"]["route_id"], "route-a")
        a = by_id["route-a"]
        self.assertEqual(a["checkpoints"][0]["eta"], "2026-11-01T12:00:00Z")
        self.assertTrue(a["checkpoints"][-1]["eta"].startswith("2026-11-01T14:00"))

    def test_heatmap_shape(self):
        r = _route("route-a", "Route A", LINE, 3600)
        fc = lambda pts, h: {p: _fake_forecast(start=self.dep.timestamp()) for p in pts}
        res = analyze(self.req, get_routes=lambda o, d: [r], fetch_forecasts=fc)
        hm = res["heatmap"]
        self.assertEqual(hm["hours"], 48)
        self.assertTrue(all(len(p[2]) == 49 for p in hm["points"]))

    def test_same_location_rejected(self):
        r = _route("route-a", "Route A", [(40, -100), (40, -100.0001)], 1)
        with self.assertRaises(ValidationError):
            analyze(self.req, get_routes=lambda o, d: [r], fetch_forecasts=lambda *a: {})


class PlanEvaluateTests(SimpleTestCase):
    """The browser-weather flow: plan -> (JSON round trip + browser weather) -> evaluate."""

    def setUp(self):
        self.dep = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        self.req = AnalysisRequest(Place(40, -100), Place(40, -98.5), self.dep, 35_000, 25)
        self.route = _route("route-a", "Route A", LINE, 7200)

    def _plan(self):
        import json

        plan = build_plan(self.req, get_routes=lambda o, d: [self.route])
        return json.loads(json.dumps(plan))  # what the browser sends back

    def _weather(self, plan, wind):
        start = plan["window"]["start"]
        return {
            p["key"]: weather.forecast_from_payload(
                {"start": start, "wind": [wind] * 100, "rain": [0] * 100, "snow": [0] * 100}
            )
            for p in plan["weather_points"]
        }

    def test_plan_lists_every_needed_weather_point(self):
        plan = self._plan()
        keys = {p["key"] for p in plan["weather_points"]}
        for cp in plan["routes"][0]["checkpoints"]:
            self.assertIn(cp["key"], keys)
        self.assertTrue(set(plan["heat_keys"]) <= keys)
        self.assertLess(plan["window"]["start"], plan["request"]["departure_epoch"])
        self.assertGreaterEqual(plan["window"]["end"], plan["request"]["departure_epoch"] + 48 * 3600)

    def test_load_changes_result_without_refetching_weather(self):
        plan = self._plan()
        fc = self._weather(plan, wind=50.0)
        heavy = evaluate(plan, fc)["routes"][0]
        self.assertFalse(heavy["drivable"])  # 50 mph with 35,000 lb -> No Travel
        plan["request"]["load_lb"] = 20_000
        light = evaluate(plan, fc)["routes"][0]
        self.assertEqual(light["miles_by_level"]["No Travel"], 0)
        self.assertGreater(light["miles_by_level"]["Severe"], 0)

    def test_segments_cover_route_and_heatmap_complete(self):
        plan = self._plan()
        res = evaluate(plan, self._weather(plan, wind=5.0))
        route = res["routes"][0]
        self.assertAlmostEqual(sum(route["miles_by_level"].values()), route["distance_mi"], delta=0.5)
        self.assertEqual(len(route["segments"]), 1)  # all Low -> one merged segment
        self.assertTrue(all(len(p[2]) == 49 for p in res["heatmap"]["points"]))

    def test_missing_or_bad_weather_rejected(self):
        plan = self._plan()
        with self.assertRaises(ValidationError):
            evaluate(plan, {})
        with self.assertRaises(ValidationError):
            weather.forecast_from_payload({"start": 0, "wind": [1], "rain": [1], "snow": [1]})
        with self.assertRaises(ValidationError):
            weather.forecast_from_payload({"start": 0, "wind": [-1, 2], "rain": [0, 0], "snow": [0, 0]})
        with self.assertRaises(ValidationError):
            evaluate({"request": {}}, {})


class RankingTests(SimpleTestCase):
    def _r(self, label, nt=0, sev=0, high=0, avg=0.0, dur=3600):
        return {"id": label, "label": label, "duration_s": dur, "avg_risk": avg,
                "miles_by_level": {"No Travel": nt, "Severe": sev, "High": high,
                                   "Moderate": 0, "Low": 0}}

    def test_priority_order(self):
        a = self._r("A", sev=10, high=0, avg=0.5, dur=1000)
        b = self._r("B", sev=0, high=80, avg=2.0, dur=9000)
        c = self._r("C", sev=0, high=20, avg=1.0, dur=5000)
        ranked = recommend.rank_routes([a, b, c])
        self.assertEqual([r["label"] for r in ranked], ["C", "B", "A"])
        self.assertTrue(ranked[0]["recommended"])

    def test_travel_time_breaks_ties(self):
        a = self._r("A", dur=5000)
        b = self._r("B", dur=4000)
        self.assertEqual(recommend.rank_routes([a, b])[0]["label"], "B")

    def test_no_travel_beats_everything(self):
        a = self._r("A", nt=30)
        b = self._r("B", sev=50)
        self.assertEqual(recommend.rank_routes([a, b])[0]["label"], "B")


class ValidationTests(SimpleTestCase):
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)

    def _body(self, **kw):
        body = {"origin": {"lat": 40, "lng": -100}, "destination": {"lat": 41, "lng": -99},
                "departure": (self.now + timedelta(hours=2)).isoformat(),
                "load_lb": 35000, "interval_mi": 25}
        body.update(kw)
        return body

    def test_ok(self):
        req = parse_request(self._body(), self.now)
        self.assertEqual(req.load_lb, 35000)

    def test_bad_inputs(self):
        for bad in [
            {"load_lb": -1}, {"load_lb": "abc"}, {"interval_mi": 7},
            {"departure": "nonsense"}, {"origin": {"lat": 200, "lng": 0}},
            {"departure": (self.now - timedelta(days=2)).isoformat()},
            {"departure": (self.now + timedelta(days=30)).isoformat()},
        ]:
            with self.assertRaises(ValidationError, msg=str(bad)):
                parse_request(self._body(**bad), self.now)


class WeatherTests(SimpleTestCase):
    def test_interpolation(self):
        f = weather.Forecast([0, 3600], [10, 30], [0, 0.2], [0, 0])
        s = f.at(1800)
        self.assertAlmostEqual(s.wind_mph, 20)
        self.assertAlmostEqual(s.rain_in_hr, 0.1)
        self.assertTrue(f.at(7200).beyond_forecast)
