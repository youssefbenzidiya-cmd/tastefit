import unittest

import cityscan_breakeven as be


def fixed(**v):
    """Degenerate inputs: every distribution collapses to one value."""
    base = {"covers_avg": 150, "demand_mult": 1.0, "ticket": 40, "days": 28, "cogs": 0.3, "other_var": 0.05,
            "staff": 22000, "other_fixed": 7000, "capex": 600000} | v
    out = {k: be.tri(x, x, x) for k, x in base.items() if k != "demand_mult"}
    out["demand_mult"] = be.lognormal(base["demand_mult"], 0.0)
    return out


class Deterministic(unittest.TestCase):
    def test_equals_the_formula(self):
        rel, rent = 1.2, 15000
        r = be.scenario(rel, rent, fixed(), draws=50)
        covers = 150 * 1.2
        revenue = covers * 40 * 28
        profit = revenue * (1 - 0.35) - 15000 - 22000 - 7000
        self.assertEqual(r["profit_p10_p50_p90"], [round(profit)] * 3)
        self.assertEqual(r["revenue_p50"], round(revenue))
        self.assertEqual(r["p_break_even"], 1.0)
        self.assertAlmostEqual(r["payback_months_p50"], round(600000 / profit, 1))
        self.assertAlmostEqual(r["rent_to_revenue_p50"], round(15000 / revenue, 3))

    def test_loss_never_breaks_even(self):
        r = be.scenario(0.2, 30000, fixed(), draws=50)
        self.assertEqual(r["p_break_even"], 0.0)
        self.assertIsNone(r["payback_months_p50"])

    def test_triangular_quantiles(self):
        spec = be.tri(10, 20, 40)
        self.assertEqual(be.quantile(spec, 0.0), 10)
        self.assertAlmostEqual(be.quantile(spec, 1.0), 40)
        self.assertAlmostEqual(be.quantile(spec, 1 / 3), 20)  # the mode sits at F = (c - a) / (b - a)


class Uncertain(unittest.TestCase):
    def setUp(self):
        self.inputs = be.defaults("casablanca", "cafe")["inputs"]

    def test_rent_lowers_break_even_probability(self):
        lo = be.scenario(1.0, 15000, self.inputs, draws=1000)
        hi = be.scenario(1.0, 30000, self.inputs, draws=1000)
        self.assertGreater(lo["p_break_even"], hi["p_break_even"])
        self.assertEqual(be.scenario(1.0, 15000, self.inputs, draws=1000), lo)  # seeded

    def test_sensitivity_shares_and_tornado(self):
        s = be.sensitivity(1.0, 15000, self.inputs, n=512)
        total = sum(x["first_order"] for x in s["sobol"])
        self.assertLess(total, 1.15)
        self.assertGreater(total, 0.6)  # profit is close to additive in logs, mostly first-order
        self.assertEqual(s["tornado"][0]["swing"], max(t["swing"] for t in s["tornado"]))
        self.assertIn(s["sobol"][0]["input"], ("covers_avg", "demand_mult", "ticket"))
        self.assertIn("depends mostly on", s["depends_most_on_en"])

    def test_no_defaults_for_unknown_city(self):
        self.assertIsNone(be.breakeven_card(1.0, [1000], "atlantis", "cafe"))
        card = be.breakeven_card(1.0, [15000, 30000], "casablanca", "cafe", draws=200)
        self.assertEqual(card["label"], "SCENARIO MODEL - not a forecast")
        self.assertEqual([r["rent"] for r in card["by_rent"]], [15000, 30000])


if __name__ == "__main__":
    unittest.main()
