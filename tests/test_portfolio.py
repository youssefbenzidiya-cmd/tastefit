"""Stable Street certificate. Values in these tests are synthetic. The reference simulation is re-implemented
here (docs/STABLE-STREET.md names /tmp/stable_street.py). This file does not import that script.
"""
import random
import unittest
from unittest import mock

import portfolio

# Trial 68 of Random(1) integer draws: 3 units in a line, 4 tenants, no fixed-rent stable assignment.
COUNTEREXAMPLE_V = [[3, 0, 3], [4, 5, 3], [2, 2, 3], [3, 4, 3]]
COUNTEREXAMPLE_A = [[0, -5, -4, 2], [-5, 0, 5, -5], [-5, -4, 0, 4], [-3, 3, 3, 0]]


def _draw(rng, n_units, n_tenants):
    values = [[rng.randint(0, 5) for _ in range(n_units)] for _ in range(n_tenants)]
    effects = [[0 if i == j else rng.randint(-5, 5) for j in range(n_tenants)] for i in range(n_tenants)]
    return values, effects


class Certificate(unittest.TestCase):
    def test_the_certificate_holds_on_random_instances(self):
        for seed in range(40):
            values, effects = _draw(random.Random(seed), 4, 6)
            edges = portfolio.line_edges(4)
            found = portfolio.local_search(values, effects, edges)
            self.assertEqual(portfolio.improving_moves(found["assignment"], values, effects, edges), [])
            replacements, swaps = portfolio.check_counts(found["assignment"], 6)
            self.assertEqual((found["replacements"], found["swaps"]), (replacements, swaps))
            self.assertAlmostEqual(portfolio.surplus(found["assignment"], values, effects, edges),
                                   portfolio.total_utility(found["assignment"], values, effects, edges))

    def test_the_potential_strictly_increases_each_step(self):
        # Greedy stand-alone values put tenant 0 beside tenant 2. That pair's joint effect is -40,
        # so replacing tenant 0 with tenant 1 raises the street surplus from -20 to 49.
        values = [[8, 1, 1], [7, 1, 1], [1, 6, 1], [1, 1, 6]]
        effects = [[0, 0, -20, 0], [0, 0, 15, 0], [-20, 15, 0, 0], [0, 0, 0, 0]]
        edges = portfolio.line_edges(3)
        found = portfolio.local_search(values, effects, edges)
        self.assertGreaterEqual(len(found["history"]), 2)
        for before, after in zip(found["history"], found["history"][1:]):
            self.assertGreater(after, before)
        self.assertEqual(portfolio.improving_moves(found["assignment"], values, effects, edges), [])

    def test_asymmetric_counterexample_needs_rent_flexibility(self):
        edges = portfolio.line_edges(3)
        note = portfolio.fixed_rent_note(COUNTEREXAMPLE_V, COUNTEREXAMPLE_A, edges)
        self.assertEqual(note, "This street needs rent flexibility to settle.")
        self.assertEqual(portfolio.fixed_rent_note(
            COUNTEREXAMPLE_V, portfolio.symmetric_part(COUNTEREXAMPLE_A), edges), "")

    def test_symmetric_part_does_not_change_surplus(self):
        values, effects = _draw(random.Random(7), 4, 5)
        edges = portfolio.line_edges(4)
        assign = portfolio.greedy(values)
        self.assertAlmostEqual(portfolio.surplus(assign, values, effects, edges),
                               portfolio.surplus(assign, values, portfolio.symmetric_part(effects), edges))

    def test_neighbour_effect_is_the_stated_uncalibrated_map(self):
        ramen = {"category": "restaurant", "fit": 100, "overlap": 80}
        pho = {"category": "restaurant", "fit": 50, "overlap": 40}
        coffee = {"category": "cafe", "fit": 50, "overlap": 40}
        # p = 0.4. a(ramen, pho) = -25 * 0.4 * 0.5 = -5. a(pho, ramen) = -25 * 0.4 * 1 = -10.
        self.assertEqual(portfolio.neighbour_effect(ramen, pho), -5)
        self.assertEqual(portfolio.neighbour_effect(pho, ramen), -10)
        self.assertEqual(portfolio.neighbour_effect(ramen, coffee), 2.5)
        self.assertEqual(portfolio.neighbour_effect(ramen, {"category": "restaurant", "fit": 50, "overlap": None}), 0)

    def test_assign_states_the_certificate_and_can_carry_the_diagnostic(self):
        units = [{"id": "A", "rent": 3000}, {"id": "B", "rent": 4000}, {"id": "C", "rent": 5000}]
        concepts = [
            {"id": "ramen", "category": "restaurant", "fit": 80, "overlap": 70},
            {"id": "pho", "category": "restaurant", "fit": 70, "overlap": 40},
            {"id": "coffee", "category": "cafe", "fit": 60, "overlap": 40},
        ]
        out = portfolio.assign(units, concepts, k=1)
        cats = [m["category"] for m in out["matches"] if m["concept"]]
        self.assertEqual(cats.count("restaurant"), 2)
        self.assertEqual(out["blocking"], [])
        self.assertIn("No improving move:", out["proof"])
        self.assertIn("replacements and", out["proof"])
        self.assertIn("swaps checked", out["proof"])
        self.assertIn("uncalibrated", out["proof"])
        self.assertIn("no measurable gain", out["proof"])
        self.assertNotIn("This street needs rent flexibility to settle.", out["proof"])
        with mock.patch("portfolio.ntu_stable_exists", return_value=False):
            flagged = portfolio.assign(units, concepts, k=1)
        self.assertTrue(flagged["needs_rent_flexibility"])
        self.assertIn("This street needs rent flexibility to settle.", flagged["proof"])
        self.assertIn("No improving move:", flagged["proof"])

    def test_two_rents_are_not_a_street_certificate(self):
        out = portfolio.assign([{"id": "A", "rent": 3000}, {"id": "B", "rent": 4000}],
                               [{"id": "ramen", "category": "restaurant", "fit": 80}], k=1)
        self.assertEqual(out["matches"], [])
        self.assertIn("at least three rents", out["proof"])

    def test_more_than_five_units_are_capped(self):
        units = [{"id": str(i), "rent": 1000 + i} for i in range(8)]
        concepts = [{"id": "ramen", "category": "restaurant", "fit": 80, "overlap": 10}]
        out = portfolio.assign(units, concepts, k=1)
        self.assertLessEqual(len(out["matches"]), 5)
        self.assertEqual(len(out["matches"]), 5)


class RentSplit(unittest.TestCase):
    def test_anchors_pay_less_and_the_landlord_total_stays_fixed(self):
        # Symmetric contributions 10, 6 and -4 on equal asking rents.
        # The anchor at unit 0 pays less. The free-rider at unit 2 pays more. The sum is 9000.
        rents = [3000, 3000, 3000]
        contributions = [10.0, 6.0, -4.0]
        got, scale = portfolio.suggested_rents(rents, contributions)
        self.assertEqual(scale, portfolio.POINT_TO_RENT)
        self.assertEqual(sum(got), 9000)
        self.assertLess(got[0], 3000)
        self.assertGreater(got[2], 3000)
        self.assertEqual(portfolio.roles_for(contributions), ["anchor", "anchor", "free-rider"])

    def test_the_role_follows_the_street_average_not_the_raw_sign(self):
        # 2 is positive, but it is below the average of 6, so that tenant pays more.
        contributions = [2.0, 8.0, 8.0]
        got, scale = portfolio.suggested_rents([3000, 3000, 3000], contributions)
        self.assertEqual(scale, portfolio.POINT_TO_RENT)
        self.assertEqual(portfolio.roles_for(contributions), ["free-rider", "anchor", "anchor"])
        self.assertGreater(got[0], 3000)
        self.assertLess(got[1], 3000)
        self.assertEqual(sum(got), 9000)

    def test_a_low_rent_shrinks_the_scale(self):
        got, scale = portfolio.suggested_rents([1, 1, 1], [0.0, 0.0, 1000.0])
        self.assertLess(scale, portfolio.POINT_TO_RENT)
        self.assertEqual(sum(got), 3)
        self.assertTrue(all(value > 0 for value in got))
        self.assertIn("Shrunk to keep every rent positive.", portfolio._split_note(scale))

    def test_the_symmetric_part_is_what_the_contribution_uses(self):
        effects = [[0, 8, 0], [2, 0, -6], [0, -2, 0]]
        edges = portfolio.line_edges(3)
        assignment = [0, 1, 2]
        direct = portfolio.neighbour_contributions(assignment, effects, edges)
        folded = portfolio.neighbour_contributions(assignment, portfolio.symmetric_part(effects), edges)
        self.assertEqual(direct, folded)
        # Edge (0, 1) contributes (8+2)/2 = 5 to each end. Edge (1, 2) contributes (-6 + -2)/2 = -4.
        self.assertEqual(direct, [5.0, 1.0, -4.0])

    def test_a_certified_mix_is_labelled_suggested_uncalibrated(self):
        units = [{"id": "A", "rent": 3000}, {"id": "B", "rent": 4000}, {"id": "C", "rent": 5000}]
        concepts = [
            {"id": "ramen", "category": "restaurant", "fit": 80, "overlap": 80},
            {"id": "coffee", "category": "cafe", "fit": 60, "overlap": 80},
            {"id": "books", "category": "retail", "fit": 40, "overlap": 80},
        ]
        out = portfolio.assign(units, concepts, k=1)
        split = out["rent_split"]
        self.assertEqual(split["label"], "suggested, uncalibrated")
        self.assertEqual(split["total_asking"], split["total_suggested"])
        self.assertIn("no interest", split["note"])
        self.assertIn("no discounting", split["note"])
        self.assertEqual(len(split["rows"]), 3)
        roles = {row["concept"]: row["role"] for row in split["rows"]}
        self.assertTrue(set(roles.values()) <= {"anchor", "free-rider", "neutral"})

    def test_equal_contributions_leave_the_asking_rents_unchanged(self):
        got, scale = portfolio.suggested_rents([2800, 4200, 5100], [0.0, 0.0, 0.0])
        self.assertEqual(scale, portfolio.POINT_TO_RENT)
        self.assertEqual(got, [2800.0, 4200.0, 5100.0])


class WhatIf(unittest.TestCase):
    def street(self):
        units = [{"id": "A", "rent": 2000}, {"id": "B", "rent": 2000}, {"id": "C", "rent": 2000}]
        concepts = [
            {"id": "ramen", "category": "restaurant", "fit": 90, "overlap": 100},
            {"id": "pho", "category": "restaurant", "fit": 90, "overlap": 100},
        ]
        return units, concepts

    def test_removing_a_cannibal_raises_the_neighbour_and_makes_no_qloo_call(self):
        units, concepts = self.street()
        out = portfolio.what_if(units, concepts, remove="pho")
        self.assertEqual(out["qloo_calls"], 0)
        by_name = {row["concept"]: row for row in out["changes"]}
        self.assertIsNone(by_name["pho"]["after"])
        self.assertGreater(by_name["ramen"]["delta"], 0)
        self.assertIn("No improving move:", out["after"]["proof"])
        self.assertFalse(out["after"]["needs_rent_flexibility"])

    def test_an_unknown_name_has_no_taste_data(self):
        units, concepts = self.street()
        out = portfolio.what_if(units, concepts, add="supermarket at the corner")
        self.assertIn("No taste data in this run", out["added_note"])
        self.assertIn("No extra Qloo call", out["added_note"])
        self.assertEqual(out["qloo_calls"], 0)
        added = next(row for row in out["changes"] if row["concept"] == "supermarket at the corner")
        self.assertIsNone(added["before"])

    def test_the_flexibility_line_is_the_stability_result(self):
        units, concepts = self.street()
        with mock.patch("portfolio.ntu_stable_exists", return_value=False):
            out = portfolio.what_if(units, concepts, remove="pho")
        self.assertTrue(out["after"]["needs_rent_flexibility"])
        self.assertIn("This street needs rent flexibility to settle.", out["after"]["proof"])

    def test_add_and_remove_together_are_rejected(self):
        units, concepts = self.street()
        with self.assertRaises(ValueError):
            portfolio.what_if(units, concepts, remove="pho", add="supermarket")


class ReferenceSimulation(unittest.TestCase):
    """Re-implementation of the missing reference script. Integer draws, line adjacency.

    The spec's 25/400 and 96.4% figures belong to the check named in docs/STABLE-STREET.md.
    This generator is not tuned to those counts, so the test locks the qualitative facts only.
    """

    def test_fixed_rent_failures_show_up_for_asymmetric_effects_only(self):
        asymmetric = symmetric = 0
        for seed in range(400):
            values, effects = _draw(random.Random(seed), 3, 4)
            edges = portfolio.line_edges(3)
            if not portfolio.ntu_stable_exists(values, effects, edges):
                asymmetric += 1
            if not portfolio.ntu_stable_exists(values, portfolio.symmetric_part(effects), edges):
                symmetric += 1
        self.assertGreater(asymmetric, 0)
        self.assertEqual(symmetric, 0)

    def test_local_search_stays_under_the_best_surplus_and_the_potential_rises(self):
        for seed in range(300):
            values, effects = _draw(random.Random(1000 + seed), 4, 6)
            edges = portfolio.line_edges(4)
            found = portfolio.local_search(values, effects, edges)
            self.assertEqual(portfolio.improving_moves(found["assignment"], values, effects, edges), [])
            for before, after in zip(found["history"], found["history"][1:]):
                self.assertGreater(after, before)
            best = max(portfolio.surplus(assign, values, effects, edges)
                       for assign in portfolio._fillings(4, 6))
            self.assertLessEqual(found["history"][-1], best + portfolio.EPS)


if __name__ == "__main__":
    unittest.main()
