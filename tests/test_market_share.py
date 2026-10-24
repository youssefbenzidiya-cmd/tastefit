"""Market-share estimate. Fixtures are synthetic; the committed aggregate is official ACS and BLS only."""
import unittest
from pathlib import Path

import market_share
import scan

PUBLISHED = {
    "all": 3945, "lt_15000": 1739, "15_29999": 1551, "30_39999": 2353, "40_49999": 2330,
    "50_69999": 2847, "70_99999": 3410, "100_149999": 4786, "150_199999": 6241, "200_plus": 8489,
}
# id, lat, lon, population, households, annual food-away-from-home dollars, unassigned households
TRACT = ["t1", 41.0, -87.0, 100, 40, 12000, 7]


def zeros():
    return {code: 0 for code in market_share.BAND_OF}


class FoodAway(unittest.TestCase):
    def test_published_cex_means_and_unassigned_bin(self):
        doc = market_share.table()
        self.assertEqual(doc["source"]["cex"]["means"], PUBLISHED)
        self.assertEqual(doc["source"]["bin_band"]["B19001_E012"], "unassigned")
        self.assertEqual(doc["source"]["cex"]["item"], "Food away from home")
        self.assertIn("summary file", doc["source"]["census_access"])
        self.assertGreater(doc["source"]["tracts"]["chicago"], 0)
        self.assertGreater(doc["source"]["tracts"]["philadelphia"], 0)

    def test_straddling_bin_adds_no_spend(self):
        counts = zeros()
        counts["E011"] = 2
        counts["E012"] = 10
        counts["E013"] = 4
        annual, unassigned, assigned = market_share.food_away_from_home(counts, PUBLISHED)
        self.assertEqual(unassigned, 10)
        self.assertEqual(assigned, 6)
        self.assertEqual(annual, 2 * PUBLISHED["50_69999"] + 4 * PUBLISHED["70_99999"])

    def test_missing_sentinel_is_not_treated_as_zero(self):
        counts = zeros()
        counts["E002"] = None
        self.assertEqual(market_share.food_away_from_home(counts, PUBLISHED), (None, None, None))
        counts["E002"] = -666666666
        self.assertIsNone(market_share.food_away_from_home(counts, PUBLISHED)[0])
        counts = zeros()
        counts["E012"] = None
        annual, unassigned, assigned = market_share.food_away_from_home(counts, PUBLISHED)
        self.assertEqual((annual, unassigned, assigned), (0, None, 0))


class ShareMath(unittest.TestCase):
    def test_no_competitor_takes_the_whole_lower_bound(self):
        e = market_share.estimate("chicago", 41.0, -87.0, tracts=[TRACT], venue_rows=[])
        self.assertEqual((e["venues"], e["share"], e["monthly_spend_usd"], e["unassigned_households"]), (0, 1, 1000, 7))
        self.assertIn("share is 1", e["note"])
        self.assertIn("not profit", e["note"])
        self.assertIn("no discounting", e["note"])

    def test_one_nearby_venue_splits_the_spend_and_a_far_one_does_not(self):
        venues = [[41.0, -87.0, "r", "x", "near"], [43.0, -87.0, "r", "x", "far"]]
        e = market_share.estimate("chicago", 41.0, -87.0, asking_rent=400, tracts=[TRACT], venue_rows=venues)
        self.assertEqual(e["venues"], 1)
        self.assertEqual(e["monthly_spend_usd"], 500)
        self.assertTrue(e["pays_rent"])
        self.assertIn("at or below", e["note"])
        over = market_share.estimate("chicago", 41.0, -87.0, asking_rent=501, tracts=[TRACT], venue_rows=venues)
        self.assertFalse(over["pays_rent"])
        self.assertIn("above this rough ceiling", over["note"])

    def test_outside_the_us_is_not_available_and_other_us_cities_say_so(self):
        self.assertEqual(market_share.estimate("paris", 48.86, 2.35)["note"], "not available")
        self.assertEqual(market_share.estimate("casablanca", 33.5, -7.6, display_name="Casablanca, Morocco")["note"],
                         "not available")
        austin = market_share.estimate("austin", 30.27, -97.74, display_name="Austin, Texas, United States")
        self.assertFalse(austin["available"])
        self.assertNotEqual(austin["note"], "not available")
        self.assertIn("Chicago and Philadelphia", austin["note"])


class ShippedPoints(unittest.TestCase):
    def test_chicago_and_philadelphia_return_a_share_in_range(self):
        for city, lat, lon in (("chicago", 41.884, -87.632), ("philadelphia", 39.9526, -75.1652)):
            e = market_share.estimate(city, lat, lon, asking_rent=4000, gate_passed=(city == "chicago"))
            self.assertTrue(e["available"], city)
            self.assertGreater(e["annual_fafh_usd"], 0)
            self.assertGreater(e["monthly_spend_usd"], 0)
            self.assertGreater(e["venues"], 0)
            self.assertEqual(e["share"], 1 / (1 + e["venues"]))
            self.assertGreater(e["share"], 0)
            self.assertLessEqual(e["share"], 1)
            self.assertEqual(e["monthly_spend_usd"], market_share.monthly_dollars(e["annual_fafh_usd"], e["venues"]))
            self.assertIn("no measurable gain", e["note"])
            self.assertIn("Rough ceiling", e["note"])
            self.assertIn("Missing mapped venues", e["note"])
            self.assertIn("ACS 2024", e["note"])
            self.assertIn("Table 1203", e["note"])
            if city == "chicago":
                self.assertNotIn("failed the map-quality check", e["note"])
            else:
                self.assertIn("failed the map-quality check", e["note"])

    def test_scan_unit_attaches_it_and_leaves_errors_as_text(self):
        code, body = scan.unit("chicago", "41.884", "-87.632")
        self.assertEqual(code, 200)
        self.assertTrue(body["market_share"]["available"])
        self.assertIn("rent_ceiling", body)
        code, body = scan.unit("paris", "48.865", "2.377")
        self.assertEqual(body["market_share"]["note"], "not available")
        code, body = scan.unit("philadelphia", "39.9526", "-75.1652")
        self.assertTrue(body["data_thin"])
        self.assertNotIn("rent_ceiling", body)
        self.assertTrue(body["market_share"]["available"])
        self.assertIn("failed the map-quality check", body["market_share"]["note"])
        code, body = scan.scan("philadelphia", "specialty coffee")
        self.assertTrue(body["data_thin"])
        self.assertNotIn("top", body)
        self.assertTrue(body["market_share"]["available"])
        self.assertIn("failed the map-quality check", body["market_share"]["note"])
        self.assertNotIn("rent_ceiling", body)
        code, body = scan.scan("casablanca", "specialty coffee")
        self.assertEqual(body["market_share"]["note"], "not available")
        code, body = scan.unit("chicago", "0", "0")
        self.assertEqual(code, 400)
        self.assertNotIn("market_share", body)

    def test_the_note_is_escaped_in_both_pages(self):
        self.assertIn("esc(u.market_share.note)", Path("index.html").read_text(encoding="utf-8"))
        self.assertIn("esc(m.note)", Path("static/app.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
