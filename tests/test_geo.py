import unittest

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


if __name__ == "__main__":
    unittest.main()
