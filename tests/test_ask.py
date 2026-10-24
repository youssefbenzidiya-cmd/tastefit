"""GET /ask: Chicago survival-vs-rent curve. No Qloo, no network, no map."""
import os
import unittest

os.environ.pop("QLOO_API_KEY", None)
import scan  # noqa: E402


class AskCurve(unittest.TestCase):
    def test_chicago_restaurant_eighty_percent_is_not_for_sale(self):
        code, d = scan.ask("chicago", "restaurant")
        self.assertEqual(code, 200)
        self.assertEqual(d["currency"], "USD")
        self.assertFalse(d["eighty_for_sale"])
        self.assertLessEqual(d["ceilings"]["80"]["rent_max"], 0)
        self.assertGreater(d["s_free"], d["city_s3"])
        self.assertEqual(d["kappa"], 0.0)
        self.assertEqual(d["location_hr"], 1.0)
        self.assertEqual(d["curve"][0][0], 0)
        self.assertGreaterEqual(d["curve"][-1][0], 8000)
        self.assertEqual(len(d["curve"]), 41)
        self.assertEqual(len(d["curve"][0]), 5)
        self.assertGreater(d["capex_default"], 0)
        self.assertGreaterEqual(d["curve"][0][4], 0)
        self.assertEqual(len(d["caps"]), 2000)
        self.assertEqual(len(d["fx"]), 2000)
        self.assertEqual(d["draws_n"], 2000)
        self.assertEqual(d["buffer_months"], 6)
        live = sum(1 for a in d["caps"] if a >= 4200) / len(d["caps"])
        curve = d["curve"]
        for i in range(1, len(curve)):
            if curve[i][0] >= 4200:
                a, b = curve[i - 1], curve[i]
                t = (4200 - a[0]) / (b[0] - a[0] or 1)
                self.assertAlmostEqual(live, a[1] + t * (b[1] - a[1]), delta=0.02)
                break
        else:
            self.fail("curve does not reach $4200")

    def test_group_and_city_are_required(self):
        self.assertEqual(scan.ask("", "restaurant")[0], 400)
        self.assertEqual(scan.ask("chicago", "sushi")[0], 400)
        self.assertEqual(scan.ask("atlantis", "restaurant")[0], 404)
