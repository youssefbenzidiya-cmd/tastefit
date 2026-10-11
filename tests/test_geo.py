import io
import json
import threading
import unittest
from unittest import mock

import geo


def pt(lat, lon, w=1.0):
    return {"name": None, "lat": lat, "lon": lon, "w": w, "address": None}


class Barycenter(unittest.TestCase):
    def test_weighted_mean(self):
        lat, lon = geo.barycenter([pt(0, 0, 1), pt(1, 1, 3)])
        self.assertAlmostEqual(lat, 0.75)
        self.assertAlmostEqual(lon, 0.75)


class Analyze(unittest.TestCase):
    demand = [pt(40.700, -73.950, 0.9), pt(40.705, -73.945, 0.8), pt(40.710, -73.955, 0.7), pt(40.702, -73.960, 0.6)]

    def test_needs_two_demand_points(self):
        self.assertIsNone(geo.analyze(self.demand[:1], []))

    def test_no_competition_stays_near_demand(self):
        r = geo.analyze(self.demand, [])
        self.assertLess(r["recommended"]["km_from_demand_center"], 1.0)
        self.assertIsNone(r["recommended"]["nearest_competitor_km"])
        self.assertGreater(r["recommended"]["opportunity_index"], 50)

    def test_competition_pushes_spot_away(self):
        free = geo.analyze(self.demand, [])
        rival = [pt(40.705, -73.952, 2.0)]
        crowded = geo.analyze(self.demand, rival)
        self.assertLess(crowded["recommended"]["opportunity_index"], free["recommended"]["opportunity_index"])
        self.assertGreater(crowded["recommended"]["nearest_competitor_km"], 0.2)
        self.assertIn("competition_center", crowded)


class Extract(unittest.TestCase):
    def test_heatmap_cell_and_place(self):
        result = {"results": [
            {"location": {"latitude": 40.7, "longitude": -73.9}, "query": {"affinity": 0.8}},
            {"name": "Cafe", "properties": {"address": "1 Main St", "geocode": {"latitude": 40.71, "longitude": -73.91}}},
            {"name": "No location"},
        ]}
        pts = geo.extract(result)
        self.assertEqual(len(pts), 2)
        self.assertEqual(pts[0]["w"], 0.8)
        self.assertEqual(pts[1]["address"], "1 Main St")


class FakeClock:
    """Monotonic clock where sleep() advances time instantly; safe to share between threads."""

    def __init__(self, start=1000.0):
        self.t, self.lock = start, threading.Lock()

    def now(self):
        with self.lock:
            return self.t

    def sleep(self, s):
        with self.lock:
            self.t += s


class Nominatim:
    """Stands in for urlopen: records each request time and answers with a synthetic hit (or none)."""

    def __init__(self, clock, hits=None):
        self.clock, self.calls, self.lock = clock, [], threading.Lock()
        self.hits = hits if hits is not None else [{"lat": "40.7", "lon": "-73.9"}]

    def __call__(self, req, timeout=None):
        with self.lock:
            self.calls.append((self.clock.now(), req.full_url, req.get_header("User-agent")))
        return io.BytesIO(json.dumps(self.hits).encode())


class Geocoding(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.api = Nominatim(self.clock)
        for target, fake in (("_clock", self.clock.now), ("_sleep", self.clock.sleep)):
            p = mock.patch.object(geo, target, fake)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("geo.urllib.request.urlopen", self.api)
        p.start()
        self.addCleanup(p.stop)
        geo._last_call[0] = -geo.MIN_INTERVAL

    def test_at_most_one_request_per_second_across_threads(self):
        def work(prefix):
            for i in range(3):
                geo.geocode(f"{prefix} {i} Main St")

        threads = [threading.Thread(target=work, args=(n,)) for n in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        times = sorted(c[0] for c in self.api.calls)
        self.assertEqual(len(times), 6)
        for a, b in zip(times, times[1:]):
            self.assertGreaterEqual(b - a, 1.0)

    def test_geocode_sends_user_agent_and_parses_hit(self):
        self.assertEqual(geo.geocode("1 Main St, Brooklyn"), (40.7, -73.9))
        _, url, ua = self.api.calls[0]
        self.assertIn("nominatim.openstreetmap.org/search", url)
        self.assertIn("q=1+Main+St%2C+Brooklyn", url)
        self.assertTrue(ua.startswith("TasteFit/"))

    def test_geocode_returns_none_on_empty_or_error(self):
        self.api.hits = []
        self.assertIsNone(geo.geocode("Nowhere"))
        with mock.patch("geo.urllib.request.urlopen", side_effect=OSError("down")):
            self.assertIsNone(geo.geocode("Somewhere"))

    def test_locate_fills_coordinates_and_uses_per_run_cache(self):
        pts = [{"name": "A", "lat": None, "lon": None, "w": 1, "address": "1 Main St"},
               {"name": "B", "lat": None, "lon": None, "w": 1, "address": "1 Main St"},
               {"name": "C", "lat": 40.71, "lon": -73.91, "w": 1, "address": None},
               {"name": "D", "lat": None, "lon": None, "w": 1, "address": None}]
        placed = geo.locate(pts, "Brooklyn")
        self.assertEqual([p["name"] for p in placed], ["A", "B", "C"])
        self.assertEqual((placed[0]["lat"], placed[0]["lon"]), (40.7, -73.9))
        self.assertEqual(len(self.api.calls), 1)
        self.assertIn("1+Main+St%2C+Brooklyn", self.api.calls[0][1])

    def test_locate_does_not_repeat_area_and_respects_limit(self):
        pts = [{"name": str(i), "lat": None, "lon": None, "w": 1, "address": f"{i} Bedford Ave, Brooklyn"} for i in range(4)]
        placed = geo.locate(pts, "brooklyn", limit=2)
        self.assertEqual(len(placed), 2)
        self.assertEqual(len(self.api.calls), 2)
        self.assertNotIn("Brooklyn%2C+brooklyn", self.api.calls[0][1])

    def test_locate_drops_points_that_cannot_be_placed(self):
        self.api.hits = []
        pts = [{"name": "A", "lat": None, "lon": None, "w": 1, "address": "Unknown"}]
        self.assertEqual(geo.locate(pts, "Brooklyn"), [])

    def test_no_cache_survives_between_runs(self):
        pts = [{"name": "A", "lat": None, "lon": None, "w": 1, "address": "1 Main St"}]
        geo.locate(pts, "Brooklyn")
        geo.locate(pts, "Brooklyn")
        self.assertEqual(len(self.api.calls), 2)


if __name__ == "__main__":
    unittest.main()
