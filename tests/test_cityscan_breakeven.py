import math
import unittest
from statistics import NormalDist

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


class Calibrated(unittest.TestCase):
    def test_each_city_uses_its_own_currency_register_and_rent_share(self):
        for city, cur, src, share in (("paris", "EUR", "SIRENE", 0.10), ("chicago", "USD", "Chicago", 0.06)):
            for profile in ("restaurant", "fast_casual", "cafe"):
                c = be.calibration(city, profile)
                self.assertEqual(c["currency"], cur)
                self.assertIn(src, c["official"]["source"])
                self.assertEqual(c["rent_share"]["mode"], share)
                self.assertTrue(0 < c["official"]["s3"] < c["official"]["s1"] < 1)
                self.assertEqual(len(c["boot"]), 100)

    def test_each_city_was_fitted_under_the_rent_share_it_documents(self):
        # kappa = 0, so the model's 3-year survival does not depend on location: the embedded theta and sigma must
        # reproduce the official rate under the city's own rent share, and miss it under the other city's.
        def s3(c, share):
            f = be.runway_factor(36, c["buffer_months"])
            d = be.survival_draws(c["inputs"], 400, rent_share=share)
            return sum(1 for m, fx, rp, z in d if c["theta"] * (math.exp(c["sigma"] * z) * m - rp) >= fx * f) / 400

        for city, other in (("paris", "chicago"), ("chicago", "paris")):
            for profile in ("restaurant", "fast_casual", "cafe"):
                c = be.calibration(city, profile)
                self.assertEqual(c["kappa"], 0.0)
                self.assertAlmostEqual(s3(c, be.rent_share(city)), c["official"]["s3"], delta=0.01)
                self.assertGreater(abs(s3(c, be.rent_share(other)) - c["official"]["s3"]), 0.03)

    def test_location_term_only_where_it_ranks_held_out_venues(self):
        for city, applied in (("paris", True), ("chicago", False)):
            c = be.calibration(city, "restaurant")
            self.assertEqual(c["location_hr"], c["location_c_min"] > 0.5)
            self.assertEqual(c["location_hr"], applied)
            busy = be.rent_ceiling_card(1.0, c, z_same=2.0, z_poi=2.0, n=200)
            self.assertEqual(busy["location_hr"] != 1.0, applied)

    def test_data_thin_and_unknown_cities_are_not_calibrated(self):
        for city in ("casablanca", "philadelphia", "atlantis"):
            self.assertIsNone(be.calibration(city, "restaurant"))

    def test_chicago_ceiling_is_a_usd_figure_below_typical_revenue(self):
        c = be.calibration("chicago", "restaurant")
        card = be.rent_ceiling_card(1.0, c, n=400)
        self.assertEqual(card["currency"], "USD")
        modes = {k: c["inputs"][k]["mode"] for k in ("ticket", "days")}
        revenue = c["theta"] * modes["ticket"] * modes["days"]
        city = card["ceilings"]["city"]
        self.assertEqual(city["target"], c["official"]["s3"])
        self.assertTrue(0 < city["ci90"][0] <= city["rent_max"] <= city["ci90"][1] < 0.25 * revenue)

    def test_chicago_corpse_cash_counts_only_draws_dead_by_year_three(self):
        c = be.calibration("chicago", "restaurant")
        draws = be.survival_draws(c["inputs"], 400, 20261011)
        caps = be.rent_capacity(c["theta"], c["sigma"], c.get("kappa") or 0.0, 1.0, draws)
        p0 = be.survival_at_rent(caps, 0, 1.0)
        p = be.survival_at_rent(caps, 4200, 1.0)
        m0 = be.corpse_months(caps, draws, 0)
        m = be.corpse_months(caps, draws, 4200)
        capex = c["inputs"]["capex"]["mode"]
        self.assertGreaterEqual(m0, 0)
        self.assertLessEqual(m, (1 - p) * 36 + 1e-9)
        self.assertGreater(capex * (1 - p) + 4200 * m, capex * (1 - p0))
        card_full = be.rent_ceiling_card(1.0, c, asking=12000, n=400)
        self.assertEqual(len(card_full["curve"][0]), 5)
        self.assertEqual(len(card_full["caps"]), 400)
        book = be.credit_book(p, m, capex, 4200, 60)
        self.assertAlmostEqual(book["drawn"], capex * (1 - p) + 4200 * m)
        self.assertAlmostEqual(book["guarantee"], (1 - p) * (capex + 4200 * 60))
        self.assertAlmostEqual(book["hurdle"], book["drawn"] / p)
        self.assertGreater(book["ttk"], 0)
        self.assertLess(book["ttk"], 36)
        sure = be.credit_book(1.0, 0.0, capex, 4200, 60)
        self.assertEqual(sure["drawn"], 0)
        self.assertIsNone(sure["ttk"])
        self.assertEqual(sure["hurdle"], 0)
        xs = be.path_losses(caps, draws, 4200, capex)
        lb = be.loss_book(xs)
        self.assertAlmostEqual(lb["el"], book["drawn"], delta=1)
        self.assertAlmostEqual(lb["el"], lb["p_dead"] * lb["ruined_mean"], delta=1)
        self.assertEqual(lb["p50"], 0)
        self.assertGreaterEqual(be.loss_book(be.path_losses(caps, draws, 0, capex))["p90"], capex - 1)
        self.assertEqual(len(card_full["fx"]), 400)
        self.assertEqual(len(card_full["fx"]), len(card_full["caps"]))


class TasteFitContract(unittest.TestCase):
    """Affine lease, coherent tail, Kelly bankroll, Vasicek book. Chicago restaurant draws."""

    def setUp(self):
        c = be.calibration("chicago", "restaurant")
        self.n = 400
        self.draws = be.survival_draws(c["inputs"], self.n, 20261011)
        self.b = c["buffer_months"]
        self.caps = be.rent_capacity(c["theta"], c["sigma"], c.get("kappa") or 0.0, 1.0, self.draws, 36, self.b)
        self.capex = c["inputs"]["capex"]["mode"]
        self.rent = 4200.0

    def test_zero_share_is_the_fixed_rent_coin(self):
        xs = be.path_losses(self.caps, self.draws, self.rent, self.capex, self.b)
        ys, harvest = be.affine_paths(self.caps, self.draws, self.rent, 0.0, self.capex, self.b)
        self.assertEqual(xs, ys)
        self.assertEqual(be.affine_capacity(self.caps, self.draws, 0.0, self.b), list(self.caps))
        H = sum(harvest) / len(harvest)
        base = be.match_base(self.caps, self.draws, 0.0, H, self.capex, self.b)
        self.assertAlmostEqual(base, self.rent, delta=2)

    def test_harvest_matched_share_buys_survival(self):
        _, h0 = be.affine_paths(self.caps, self.draws, self.rent, 0.0, self.capex, self.b)
        H0 = sum(h0) / len(h0)
        p0 = be.survival_at_rent(self.caps, self.rent)
        p_prev, el_prev = p0, be.loss_book(be.path_losses(self.caps, self.draws, self.rent, self.capex, self.b))["el"]
        for share in (0.08, 0.20, 0.40):
            base = be.match_base(self.caps, self.draws, share, H0, self.capex, self.b)
            losses, harvest = be.affine_paths(self.caps, self.draws, base, share, self.capex, self.b)
            self.assertAlmostEqual(sum(harvest) / len(harvest), H0, delta=50)
            p = 1.0 - be.loss_book(losses)["p_dead"]
            el = be.loss_book(losses)["el"]
            self.assertGreater(p, p_prev - 1e-9)
            self.assertLess(el, el_prev + 1)
            p_prev, el_prev = p, el
        self.assertGreater(p_prev, 0.80)
        self.assertLess(be.match_base(self.caps, self.draws, 0.40, H0, self.capex, self.b), 0)

    def test_pure_percentage_rent_without_lowering_the_floor_kills_more(self):
        p0 = be.survival_at_rent(self.caps, self.rent)
        losses, _ = be.affine_paths(self.caps, self.draws, self.rent, 0.20, self.capex, self.b)
        self.assertLess(1.0 - be.loss_book(losses)["p_dead"], p0)

    def test_expected_shortfall_is_the_mean_of_the_worst_tail(self):
        xs = be.path_losses(self.caps, self.draws, self.rent, self.capex, self.b)
        lb = be.loss_book(xs)
        es95 = be.expected_shortfall(xs, 0.95)
        self.assertGreaterEqual(es95, lb["p90"] - 1e-6)
        self.assertGreater(es95, lb["el"])
        self.assertAlmostEqual(be.expected_shortfall(xs, 0.0), lb["el"], delta=1)

    def test_distance_to_default_and_kelly_and_vasicek(self):
        dd = be.distance_to_default(self.caps, self.rent)
        p = be.survival_at_rent(self.caps, self.rent)
        self.assertGreater(dd["sd"], 0)
        self.assertAlmostEqual(dd["normal_pd"], NormalDist().cdf(-dd["dd"]), places=12)
        self.assertLess(abs(dd["normal_pd"] - (1 - p)), 0.08)
        self.assertIsNone(be.kelly_bankroll(p, self.capex / p, self.capex))
        self.assertAlmostEqual(be.kelly_bankroll(0.6, 900000, 450000), 2_250_000)
        self.assertAlmostEqual(be.vasicek_k(0.36, 0.0, 0.999), 0.36, places=12)
        self.assertGreater(be.vasicek_k(0.36, be.ASSET_RHO, 0.999), 0.36)
        self.assertAlmostEqual(be.ASSET_RHO, 0.12)


if __name__ == "__main__":
    unittest.main()
