"""Taste Gap Index: demand over-index, Gamma-shrunk supply ratio, quadrant labels, OSM venue matching."""
import gzip
import json
import os
import random
import tempfile
import unittest
from unittest import mock

import cityscan
import tgi

GRID = cityscan.make_grid([41.85, -87.75, 41.95, -87.60])
POINT = (41.90, -87.68)


def venue(lat, lon, kind="r", cuisine="american", name=""):
    return [lat, lon, kind, cuisine, name]


def city(near_ramen=0, near_coffee=8, seed=3):
    """Food venues spread over the grid, with a chosen number of ramen shops and cafes next to POINT."""
    rng = random.Random(seed)
    vs = [venue(rng.uniform(41.86, 41.94), rng.uniform(-87.74, -87.61)) for _ in range(600)]
    vs += [venue(rng.uniform(41.86, 41.88), rng.uniform(-87.74, -87.61), cuisine="ramen") for _ in range(30)]
    vs += [venue(rng.uniform(41.86, 41.94), rng.uniform(-87.74, -87.61), kind="c", cuisine="") for _ in range(60)]
    vs += [venue(POINT[0] + 0.0005 * i, POINT[1], cuisine="ramen") for i in range(near_ramen)]
    vs += [venue(POINT[0], POINT[1] + 0.0005 * i, kind="c", cuisine="coffee_shop") for i in range(near_coffee)]
    vs += [venue(POINT[0] - 0.0005 * i, POINT[1]) for i in range(30)]
    vs += [venue(POINT[0] + 0.001, POINT[1] + 0.001, name="Café Leopardi"), venue(41.86, -87.74, name="Far Away Diner")]
    return tgi.Venues(GRID, vs)


class OverindexTest(unittest.TestCase):
    def test_affine_invariance_per_call(self):
        local, city_ = {"a": 0.9, "b": 0.5, "c": 0.1}, {"a": 0.2, "b": 0.6, "c": 0.4}
        d1 = tgi.demand_overindex(local, city_)
        d2 = tgi.demand_overindex({k: 3 * v + 7 for k, v in local.items()}, {k: 0.5 * v - 2 for k, v in city_.items()})
        for k in d1:
            self.assertAlmostEqual(d1[k], d2[k])

    def test_identical_scores_give_zero(self):
        s = {"a": 0.9, "b": 0.5, "c": 0.1}
        self.assertEqual(set(tgi.demand_overindex(s, dict(s)).values()), {0.0})
        self.assertEqual(tgi.demand_overindex({"a": 1, "b": 1}, {"a": 1, "b": 1}), {"a": 0.0, "b": 0.0})  # zero spread

    def test_needs_two_shared_concepts(self):
        self.assertEqual(tgi.demand_overindex({"a": 1}, {"a": 2, "b": 1}), {})

    def test_rank_scores_use_the_calls_affinity_or_position(self):
        by_id = {"B1": ["ramen"], "B2": ["coffee", "cafe"]}
        res = {"results": [{"entity_id": "B2", "affinity": 0.4}, {"entity_id": "B1", "affinity": 0.9}, {"entity_id": "X"}]}
        self.assertEqual(tgi.rank_scores(res, by_id), {"ramen": 0.9, "coffee": 0.4, "cafe": 0.4})
        res = {"results": [{"entity_id": "B1"}, {"entity_id": "B2"}]}
        self.assertEqual(tgi.rank_scores(res, by_id), {"ramen": 0.0, "coffee": -1.0, "cafe": -1.0})
        self.assertEqual(tgi.rank_scores(None, by_id), {})


class SupplyTest(unittest.TestCase):
    def test_shrinkage_toward_the_prior(self):
        a, b = 4.0, 4.0  # prior mean 1
        theta0, _ = tgi.supply_ratio(0, 0.5, a, b)
        theta1, _ = tgi.supply_ratio(1, 0.5, a, b)
        self.assertAlmostEqual(theta0, 4 / 4.5)
        self.assertAlmostEqual(theta1, 5 / 4.5)  # raw 2.0 pulled most of the way back to 1
        big, _ = tgi.supply_ratio(40, 20, a, b)
        self.assertAlmostEqual(big, 44 / 24)  # plenty of data: close to raw 2.0

    def test_probability_bounds_and_direction(self):
        for n, e in [(0, 0.0), (0, 10), (30, 5), (5, 5)]:
            theta, p = tgi.supply_ratio(n, e, 2.0, 2.0, draws=500)
            self.assertGreater(theta, 0)
            self.assertTrue(0 <= p <= 1)
        self.assertGreater(tgi.supply_ratio(0, 10, 2, 2, draws=500)[1], 0.95)
        self.assertLess(tgi.supply_ratio(30, 5, 2, 2, draws=500)[1], 0.05)

    def test_seeded_draws_are_reproducible(self):
        self.assertEqual(tgi.supply_ratio(2, 3, 1.5, 1.2), tgi.supply_ratio(2, 3, 1.5, 1.2))

    def test_gamma_prior_moments_and_floor(self):
        a, b = tgi.gamma_prior([0, 4, 1, 3], [2, 2, 2, 2])
        self.assertAlmostEqual(a / b, 1.0)
        # Poisson-like counts: variance floored, so the prior is very tight and the ratio is fully shrunk.
        a, b = tgi.gamma_prior([1, 1, 1, 1], [1, 1, 1, 1])
        self.assertGreater(a, 1e4)
        self.assertAlmostEqual(tgi.supply_ratio(0, 5, a, b)[0], 1.0, places=2)
        self.assertIsNone(tgi.gamma_prior([0, 0], [1, 1]))
        self.assertIsNone(tgi.gamma_prior([1], [0]))


class LabelTest(unittest.TestCase):
    def test_all_quadrants_reachable(self):
        cases = {(1, 0.9): "white space", (1, 0.1): "proven demand, crowded", (-1, 0.9): "empty for a reason",
                 (-1, 0.1): "avoid", (0, 0.9): "neutral", (1, 0.5): "neutral"}
        for (d, p), want in cases.items():
            self.assertEqual(tgi.label(d, p), want)
        self.assertIsNone(tgi.label(None, 0.9))

    def test_score_uses_the_stated_priors(self):
        self.assertEqual(tgi.score(0, 1), 50)
        self.assertGreater(tgi.score(1, 0.5), tgi.score(1, 2))
        self.assertGreater(tgi.score(1, 1), tgi.score(-1, 1))
        self.assertEqual(tgi.score(None, None), 50)


class VenueTest(unittest.TestCase):
    def test_cuisines_for_catalog_and_fallback(self):
        self.assertIn("vietnamese", tgi.cuisines_for("Vietnamese pho"))
        self.assertIn("ramen", tgi.cuisines_for("Ramen counter", "Ramen"))
        self.assertIn("burger", tgi.cuisines_for("smash burgers"))
        self.assertEqual(tgi.cuisines_for("salad bowls"), tgi.CUISINES["salad"])
        self.assertEqual(tgi.cuisines_for("xyzzy concept"), set())

    def test_matches_cuisine_lists_and_plain_cafes(self):
        self.assertTrue(tgi.matches(venue(0, 0, cuisine="sushi;ramen"), {"ramen"}))
        self.assertFalse(tgi.matches(venue(0, 0, cuisine="sushi"), {"ramen"}))
        self.assertTrue(tgi.matches(venue(0, 0, kind="c", cuisine=""), tgi.CUISINES["specialty coffee"]))
        self.assertTrue(tgi.matches(venue(0, 0, kind="b", cuisine=""), tgi.CUISINES["bakery"]))

    def test_supply_finds_gap_and_crowding(self):
        v = city(near_ramen=0, near_coffee=12)
        ramen = v.supply(tgi.CUISINES["ramen"], *POINT, draws=1000)
        coffee = v.supply(tgi.CUISINES["specialty coffee"], *POINT, draws=1000)
        self.assertEqual(ramen["n"], 0)
        self.assertGreater(ramen["expected"], 0)
        self.assertGreater(coffee["n"], coffee["expected"])
        self.assertGreater(ramen["p_under"], coffee["p_under"])
        self.assertLess(coffee["theta"], coffee["raw"])  # shrunk toward the city average
        self.assertIsNone(v.supply(set(), *POINT))
        self.assertIsNone(v.supply({"nonexistent"}, *POINT))

    def test_low_coverage_when_the_prior_dominates(self):
        v = city(near_ramen=0, near_coffee=12)
        s = v.supply(tgi.CUISINES["ramen"], *POINT, draws=200)
        self.assertEqual(s["coverage"], "low" if s["prior_weight"] >= tgi.LOW_COVERAGE else "ok")
        self.assertTrue(0 < s["prior_weight"] <= 1)
        flat = tgi.Venues(GRID, [venue(41.86 + 0.0001 * i, -87.74, cuisine="ramen" if i % 10 == 0 else "") for i in range(400)])
        self.assertEqual(flat.supply({"ramen"}, 41.87, -87.74, draws=200)["coverage"], "low")  # Poisson-like: floored prior

    def test_find_named_venue(self):
        v = city()
        self.assertEqual(v.find("Leopardi", *POINT), (True, "Café Leopardi"))
        self.assertEqual(v.find("Far Away Diner", *POINT)[0], False)  # exists, but not within 500 m
        self.assertEqual(v.find("Totally Invented Bistro", *POINT), (False, None))
        self.assertEqual(v.find("", *POINT), (False, None))

    def test_compact_keeps_food_only(self):
        els = [{"type": "node", "lat": 1.0, "lon": 2.0, "tags": {"amenity": "restaurant", "cuisine": "Ramen", "name": "X"}},
               {"type": "way", "center": {"lat": 1.5, "lon": 2.5}, "tags": {"shop": "bakery"}},
               {"type": "node", "lat": 1.0, "lon": 2.0, "tags": {"amenity": "bar"}},
               {"type": "way", "tags": {"amenity": "cafe"}}]
        self.assertEqual(tgi.compact(els), [[1.0, 2.0, "r", "ramen", "X"], [1.5, 2.5, "b", "", ""]])

    def test_query_dates_the_snapshot(self):
        q = tgi.overpass_food_query([41.0, -88.0, 42.0, -87.0], "2023-01-01")
        self.assertIn('[date:"2023-01-01T00:00:00Z"]', q)
        self.assertNotIn("bar", q.replace("[bbox", ""))
        self.assertNotIn("date", tgi.overpass_food_query([41.0, -88.0, 42.0, -87.0]))

    def test_load_reads_file_and_misses_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "{slug}.json.gz")
            with gzip.open(path.format(slug="testville"), "wt") as f:
                json.dump({"grid": GRID, "venues": [venue(*POINT)]}, f)
            with mock.patch.object(tgi, "FOOD_FILE", path), mock.patch.dict(tgi._cache, clear=True):
                self.assertEqual(len(tgi.load("Testville").venues), 1)
                self.assertIsNone(tgi.load("nowhere"))
                self.assertIsNone(tgi.load("../etc"))

    def test_shipped_venue_files_are_osm_points_only(self):
        folder = os.path.dirname(tgi.FOOD_FILE)
        for name in os.listdir(folder):
            with gzip.open(os.path.join(folder, name), "rt") as f:
                d = json.load(f)
            self.assertEqual(set(d), {"slug", "as_of", "source", "grid", "venues"})
            self.assertIn("OpenStreetMap", d["source"])
            self.assertTrue(all(len(v) == 5 and v[2] in "rcfib" for v in d["venues"]))


if __name__ == "__main__":
    unittest.main()
