"""Footfall weights, pre-window aggregates, and the evidence sentence. No network and no Qloo."""
import unittest

import backtest_chicago as bt
import evidence
import footfall
import ledger
import tenant

DOC = {
    "datasets": [
        {"id": "m6dm-c72p", "start": "2022-01-01", "end": "2022-12-31"},
        {"id": "fg6s-gzvg", "start": "2019-01-01", "end": "2019-12-31"},
        {"id": "5neh-572f", "start": "2022-01-01", "end": "2022-12-31"},
    ],
    "community_areas": [
        {"number": "24", "name": "WEST TOWN", "evening_dropoffs": 80, "weekend_dropoffs": 10,
         "divvy_ends": 10, "cta_boardings": 10},
        {"number": "41", "name": "HYDE PARK", "evening_dropoffs": 10, "weekend_dropoffs": 10,
         "divvy_ends": 10, "cta_boardings": 80},
    ],
    "backtest_areas": [
        {"area": "Wicker Park", "community_area_number": "24", "community_area_name": "WEST TOWN"},
        {"area": "Hyde Park", "community_area_number": "41", "community_area_name": "HYDE PARK"},
    ],
}


class Weights(unittest.TestCase):
    def test_stated_weights_sum_to_one_and_are_not_all_the_same(self):
        rows = list(footfall.WEIGHTS.values())
        for row in rows:
            self.assertAlmostEqual(sum(row), 1.0)
            self.assertEqual(len(row), len(footfall.COMPONENTS))
        self.assertGreater(len(set(rows)), 1)

    def test_a_later_window_is_rejected(self):
        bad = {"datasets": [{"id": "x", "start": "2022-01-01", "end": "2023-01-01"}]}
        with self.assertRaises(ValueError):
            footfall.assert_before_window(bad)

    def test_mix_changes_the_concept_order(self):
        dinner = footfall.concept_score(DOC, "24", "ramen shop")
        daytime = footfall.concept_score(DOC, "24", "specialty coffee")
        self.assertGreater(dinner, daytime)
        dinner_h = footfall.concept_score(DOC, "41", "ramen shop")
        daytime_h = footfall.concept_score(DOC, "41", "specialty coffee")
        self.assertGreater(daytime_h, dinner_h)

    def test_signal_cites_the_portal_and_is_not_a_door_count(self):
        sig = footfall.signal(DOC, "Wicker Park, Chicago")
        self.assertEqual(sig["label"], "Demand already there")
        self.assertIn("m6dm-c72p", sig["sentence"])
        self.assertIn("2022-01-01", sig["sentence"])
        self.assertIn("not a vetted address", sig["sentence"])
        self.assertIsNone(footfall.signal(DOC, "Oberkampf, Paris"))

    def test_south_loop_is_not_the_loop(self):
        doc = footfall.load()
        self.assertIsNone(footfall.for_text(doc, "South Loop, Chicago"))
        self.assertIsNone(tenant.footfall_signal("South Loop", "Chicago"))
        loop = footfall.for_text(doc, "The Loop, Chicago")
        self.assertEqual(loop["name"].casefold(), "loop")
        self.assertEqual(footfall.for_text(doc, "West Loop, Chicago")["name"].casefold(), "near west side")


class BacktestOrders(unittest.TestCase):
    def test_three_variants_and_no_qloo(self):
        table = {a: {c: [1, 1 if c == "ramen shop" else 0] for c in bt.CONCEPTS} for a in ("Wicker Park", "Hyde Park")}
        supply = {a: {c: (1.0, 0.5) for c in bt.CONCEPTS} for a in table}
        orders = bt.footfall_orders(table, [("Wicker Park", 0, 0), ("Hyde Park", 0, 0)], supply, DOC)
        self.assertEqual(orders["footfall"]["Wicker Park"][0], "ramen shop")
        self.assertLess(orders["footfall"]["Hyde Park"].index("specialty coffee"),
                        orders["footfall"]["Hyde Park"].index("ramen shop"))
        self.assertIn("taqueria", orders["footfall_prior"]["Wicker Park"])
        self.assertIsNotNone(orders["footfall_tgi"]["Wicker Park"])
        report = bt.footfall_report(table, orders, ["Wicker Park", "Hyde Park"], 0.677, DOC["datasets"], "2023-01-01")
        self.assertEqual(report["qloo_calls"], 0)
        self.assertIn("footfall_minus_prior_ci90", report)

    def test_evidence_does_not_call_a_loss_a_gain(self):
        doc = {"published_citywide_hit_at_k": 0.677, "methods": {
            "prior": {"hit_at_k": 0.677},
            "footfall": {"hit_at_k": 0.4},
            "footfall_tgi": {"hit_at_k": 0.5},
            "footfall_prior": {"hit_at_k": 0.6},
        }, "footfall_minus_prior_ci90": [-0.4, -0.1], "footfall_tgi_minus_prior_ci90": [-0.3, -0.05],
               "footfall_prior_minus_prior_ci90": [-0.2, -0.01]}
        html = evidence.page(chicago={"methods": {}}, tgi={"methods": {}}, bias={}, ledger_doc={"batches": [{}]},
                             footfall=doc)
        self.assertIn("No footfall variant beats the citywide 68%", html)
        self.assertIn("Footfall alone hit 40%", html)
        self.assertNotIn("No measurable gain", html)

    def test_a_point_estimate_above_68_without_a_clear_interval_is_not_a_win(self):
        doc = {"published_citywide_hit_at_k": 0.677, "methods": {
            "prior": {"hit_at_k": 0.677}, "footfall": {"hit_at_k": 0.8},
            "footfall_tgi": {"hit_at_k": 0.5}, "footfall_prior": {"hit_at_k": 0.5},
        }, "footfall_minus_prior_ci90": [-0.1, 0.2], "footfall_tgi_minus_prior_ci90": [-0.2, -0.01],
               "footfall_prior_minus_prior_ci90": [-0.2, -0.01]}
        html = evidence._footfall_section(doc)
        self.assertIn("not a gain", html)
        self.assertNotIn("sits above zero", html)


class CommittedFile(unittest.TestCase):
    def test_the_committed_windows_end_before_the_backtest(self):
        doc = footfall.load()
        footfall.assert_before_window(doc)
        self.assertEqual(len(doc["community_areas"]), 77)
        self.assertEqual(len(doc["backtest_areas"]), 12)
        ids = {ds["id"] for ds in doc["datasets"]}
        self.assertEqual(ids, {"m6dm-c72p", "fg6s-gzvg", "5neh-572f"})
        self.assertIn("no divvy gender", doc["personal_data"].lower())
        allowed = {"number", "name", "evening_dropoffs", "weekend_dropoffs", "divvy_ends", "cta_boardings"}
        for row in doc["community_areas"]:
            self.assertEqual(set(row), allowed)
        self.assertIn("not a vetted address", footfall.signal(doc, "Wicker Park, Chicago")["sentence"])
        self.assertGreater(sum(r["evening_dropoffs"] for r in doc["community_areas"]), 0)


class Memo(unittest.TestCase):
    def test_the_sentence_does_not_change_the_five_field_fingerprint(self):
        entry = {"area": "Wicker Park, Chicago", "concept": "ramen shop", "verdict": "unscored",
                 "ts": "2026-10-14T15:05:00Z",
                 "shortlist": [{"concept": c, "verdict": "unscored"} for c in (
                     "ramen shop", "Korean fried chicken", "Mediterranean bowls", "specialty coffee",
                     "salad bowls", "taqueria")]}
        before = ledger.fingerprint(entry)
        signal = footfall.signal(DOC, "Wicker Park, Chicago")
        memo = tenant.ensure_footfall("# Memo\n## Limits\n- Taste.\n" + tenant.BACKTEST_NOTE, signal)
        self.assertIn("Demand already there", memo)
        self.assertTrue(memo.endswith(tenant.BACKTEST_NOTE))
        self.assertIsNone(tenant.footfall_signal("Downtown", "Austin, TX"))
        self.assertIsNone(tenant.footfall_signal("Hyde Park", "London"))
        chicago = tenant.footfall_signal("Wicker Park", "Chicago")
        self.assertIn("West Town", chicago["sentence"])
        self.assertEqual(ledger.fingerprint(entry), before)
        self.assertEqual(before, ledger.fingerprint({**entry, "footfall": signal["sentence"]}))
