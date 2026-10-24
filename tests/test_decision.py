"""Rent-first verdict, exact Shapley attribution, EVPI / EVPPI (spec Upgrades 3 and 4)."""
import math
import random
import unittest

import decision as d

ROW = {"viable": True, "rent_max": 3000.0, "ci90": [2500.0, 3600.0]}


class VerdictTest(unittest.TestCase):
    def test_affordability(self):
        self.assertEqual(d.affordability(ROW, 2800), "ok")
        self.assertEqual(d.affordability(ROW, 3200), "stretch")
        self.assertEqual(d.affordability(ROW, 4000), "over")
        self.assertEqual(d.affordability({"viable": False}, 100), "over")
        self.assertIsNone(d.affordability(ROW, None))
        self.assertIsNone(d.affordability(None, 2800))

    def test_lexicographic_table(self):
        self.assertEqual(d.verdict("over", strong_taste=True), "NO-GO")  # taste cannot rescue the rent
        self.assertEqual(d.verdict("ok", strong_taste=True), "GO")
        self.assertEqual(d.verdict("stretch", strong_taste=True), "ADJUST")
        self.assertEqual(d.verdict(None, strong_taste=True), "GO")
        self.assertEqual(d.verdict("ok", strong_taste=False), "ADJUST")
        self.assertEqual(d.verdict("ok", strong_taste=True, avoid=True), "NO-GO")
        self.assertEqual(d.verdict("stretch", strong_taste=False), "ADJUST")  # weak taste repositions, it does not veto


class ShapleyTest(unittest.TestCase):
    def f(self, beta):
        return lambda v: d.sigmoid(sum(beta[k] * v[k] for k in v))

    def test_efficiency(self):
        x, base = {"a": 1.2, "b": -0.4, "c": 0.7, "e": 2.0}, dict.fromkeys("abce", 0.0)
        f = self.f({"a": 1, "b": -1, "c": 0.5, "e": 0.3})
        phi = d.shapley(f, x, base)
        self.assertAlmostEqual(sum(phi.values()), f(x) - f(base), places=12)

    def test_dummy_and_symmetry(self):
        x, base = {"a": 1.0, "b": 1.0, "c": 3.0}, dict.fromkeys("abc", 0.0)
        phi = d.shapley(self.f({"a": 0.8, "b": 0.8, "c": 0.0}), x, base)
        self.assertAlmostEqual(phi["c"], 0.0, places=12)
        self.assertAlmostEqual(phi["a"], phi["b"], places=12)

    def test_additive_identity_link(self):
        beta, x, base = {"a": 2.0, "b": -1.0, "c": 0.5}, {"a": 1.0, "b": 3.0, "c": -2.0}, {"a": 0.5, "b": 1.0, "c": 0.0}
        phi = d.shapley(lambda v: sum(beta[k] * v[k] for k in v), x, base)
        for k in beta:
            self.assertAlmostEqual(phi[k], beta[k] * (x[k] - base[k]), places=12)

    def test_explain_shares(self):
        x, base = d.features(overindex=1.0, theta=0.5, overlap=80, overlap_median=60, rent_max=3000, asking=2700)
        out = d.explain(x, base)
        self.assertEqual(out["baseline"], 50.0)
        self.assertAlmostEqual(sum(out["phi"].values()), out["score"] - out["baseline"], places=0)
        self.assertGreater(out["phi"]["supply"], 0)  # under-supplied: theta < 1 helps
        self.assertTrue(0 < out["qloo_share"] < 1)
        x, base = d.features()
        self.assertEqual(d.explain(x, base)["qloo_share"], None)
        self.assertEqual(d.features(rent_max=1000, asking=5000)[0]["rent"], -1.0)


class ValueOfInformationTest(unittest.TestCase):
    def test_evpi_properties(self):
        self.assertEqual(d.evpi([1.0, 2.0, 3.0]), 0.0)
        self.assertGreater(d.evpi([-1.0, 1.0, -2.0, 2.0]), 0)

    def test_evppi_independent_and_deterministic(self):
        rng = random.Random(1)
        x1 = [rng.gauss(0, 1) for _ in range(2000)]
        noise = [rng.gauss(0, 1) for _ in range(2000)]
        pis = [a * 1000 for a in x1]
        total = d.evpi(pis)
        self.assertAlmostEqual(d.evppi(pis, x1), total, delta=0.05 * total)  # pi = g(X1): knowing X1 is everything
        # Binned EVPPI is biased upward for an irrelevant input (bin-mean noise ~ sd / sqrt(N / B)); with pi centred
        # on 0, the worst case, that is under 10% of EVPI here, far below the informative input.
        self.assertLess(d.evppi(pis, noise), 0.10 * total)

    def test_value_of_information_ranks_checks(self):
        v = d.value_of_information("chicago", "restaurant", 1.0, 6000)
        self.assertEqual(v["currency"], "USD")
        self.assertGreaterEqual(v["evpi"], v["checks"][0]["evppi"])
        self.assertEqual(len(v["checks"]), len(d.CHECKS))
        self.assertEqual(v["checks"][0]["input"], "covers_day")
        self.assertTrue(v["checks"][0]["above_noise"])
        self.assertFalse(any(c["above_noise"] for c in v["checks"] if c["evppi"] <= 2 * v["noise_floor"]))
        self.assertEqual(v["checks"], sorted(v["checks"], key=lambda c: -c["evppi"]))
        self.assertEqual(v, d.value_of_information("chicago", "restaurant", 1.0, 6000))  # seeded
        self.assertIsNone(d.value_of_information("philadelphia", "restaurant", 1.0, 6000))
        self.assertIsNone(d.value_of_information("paris", "other", 1.0, 6000))
        self.assertIsNone(d.value_of_information("paris", "restaurant", None, 6000))
        self.assertTrue(math.isfinite(v["p_profit"]))


if __name__ == "__main__":
    unittest.main()
