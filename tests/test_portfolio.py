"""Gale-Shapley stability. Preferences in these tests are synthetic."""
import unittest

import portfolio


class DeferredAcceptance(unittest.TestCase):
    def test_the_classic_two_by_two_has_no_blocking_pair(self):
        # m1 proposes to w1 then w2; w1 prefers m2, so the stable match is m2-w1 and m1-w2.
        unit_prefs = {"m1": ["w1", "w2"], "m2": ["w1", "w2"]}
        concept_prefs = {"w1": ["m2", "m1"], "w2": ["m1", "m2"]}
        matched = portfolio.deferred_acceptance(unit_prefs, concept_prefs, k=2)
        self.assertEqual(matched, {"m1": "w2", "m2": "w1"})
        self.assertEqual(portfolio.blocking_pairs(matched, unit_prefs, concept_prefs, k=2), [])

    def test_an_unstable_swap_is_reported(self):
        unit_prefs = {"m1": ["w1", "w2"], "m2": ["w1", "w2"]}
        concept_prefs = {"w1": ["m2", "m1"], "w2": ["m1", "m2"]}
        unstable = {"m1": "w1", "m2": "w2"}
        self.assertIn(("m2", "w1"), portfolio.blocking_pairs(unstable, unit_prefs, concept_prefs, k=2))

    def test_category_cap_leaves_no_blocking_pair(self):
        units = [{"id": "A", "rent": 3000}, {"id": "B", "rent": 4000}, {"id": "C", "rent": 5000}]
        concepts = [
            {"id": "ramen", "category": "restaurant", "fit": 80, "rent_first": False, "rent_row": None},
            {"id": "pho", "category": "restaurant", "fit": 70, "rent_first": False, "rent_row": None},
            {"id": "coffee", "category": "cafe", "fit": 60, "rent_first": False, "rent_row": None},
        ]
        out = portfolio.assign(units, concepts, k=1)
        cats = [m["category"] for m in out["matches"] if m["concept"]]
        self.assertEqual(cats.count("restaurant"), 1)
        self.assertEqual(out["blocking"], [])
        self.assertIn("No blocking pair", out["proof"])
        self.assertIn("Gale and Shapley (1962)", out["proof"])
        self.assertIn("no measurable gain", out["proof"])

    def test_one_rent_is_not_a_portfolio(self):
        out = portfolio.assign([{"id": "A", "rent": 3000}], [{"id": "ramen", "category": "restaurant", "fit": 80}], k=1)
        self.assertEqual(out["matches"], [])
        self.assertIn("at least two rents", out["proof"])

    def test_more_than_five_units_are_capped(self):
        units = [{"id": str(i), "rent": 1000 + i} for i in range(8)]
        concepts = [{"id": "ramen", "category": "restaurant", "fit": 80, "rent_first": False, "rent_row": None}]
        out = portfolio.assign(units, concepts, k=1)
        self.assertLessEqual(len(out["matches"]), 5)


if __name__ == "__main__":
    unittest.main()
