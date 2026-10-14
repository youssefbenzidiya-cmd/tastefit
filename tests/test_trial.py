"""Pop-up length, rent-ceiling threshold, and a ledger outcome that leaves the Chicago hash alone."""
import unittest

import cityscan_breakeven as cb
import ledger
import tenant
import trial


class Threshold(unittest.TestCase):
    def test_mode_profit_is_zero_at_the_ceiling(self):
        defaults = cb.defaults("chicago", "restaurant")
        rent, relative = 6000, 1.0
        solved = trial.threshold_for("covers_day", defaults["inputs"], relative, rent)
        self.assertIsNotNone(solved)
        covers, direction = solved
        self.assertEqual(direction, "at_least")
        values = {k: trial.central(defaults["inputs"][k]) for k in cb.PARAM_ORDER}
        values["covers_avg"] = covers / (relative * values["demand_mult"])
        self.assertAlmostEqual(cb.monthly(values, relative, rent)["profit"], 0.0, places=6)

    def test_a_higher_ceiling_needs_more_covers(self):
        defaults = cb.defaults("chicago", "restaurant")
        low, _ = trial.threshold_for("covers_day", defaults["inputs"], 1.0, 4000)
        high, _ = trial.threshold_for("covers_day", defaults["inputs"], 1.0, 8000)
        self.assertGreater(high, low)

    def test_weeks_stay_inside_four_to_eight_with_no_discounting(self):
        self.assertEqual(trial.weeks_for(None), 6)
        one = {"evpi": 100, "checks": [{"input": "covers_day", "evppi": 80, "above_noise": True}]}
        two = {"evpi": 100, "checks": [
            {"input": "covers_day", "evppi": 40, "above_noise": True},
            {"input": "ticket", "evppi": 30, "above_noise": True}]}
        self.assertEqual(trial.weeks_for(one), 4)
        self.assertEqual(trial.weeks_for(two), 8)

    def test_the_range_line_uses_covers_days_percent_and_currency(self):
        self.assertEqual(trial._format_value("covers_day", 42.5, "USD"), "42.5 covers/day")
        self.assertEqual(trial._format_value("days", 22.0, "USD"), "22.0 days/month")
        self.assertEqual(trial._format_value("cogs", 0.312, "USD"), "31.2%")
        self.assertEqual(trial._format_value("other_var", 0.08, "USD"), "8.0%")
        self.assertEqual(trial._format_value("ticket", 18, "USD"), "18 USD")


class Plan(unittest.TestCase):
    def test_plan_and_outcome_are_separate_from_the_chicago_fingerprint(self):
        defaults = cb.defaults("chicago", "restaurant")
        rent_row = {"viable": True, "rent_max": 6000, "relative_demand": 1.0, "ci90": [4000, 9000], "group": "restaurant"}
        concept = {"concept": "ramen shop", "rent_group": "restaurant"}
        voi = {"evpi": 200, "checks": [{"input": "covers_day", "label": "covers", "evppi": 150, "above_noise": True}]}
        plan = trial.recommend("Wicker Park, Chicago", concept, "chicago", rent_row, 7000, voi, "2026-10-14T22:00:00Z")
        self.assertEqual(plan["weeks"], 4)
        self.assertIn("no interest and no discounting", plan["sentence"])
        self.assertIn("stated cost assumptions, not your figures", plan["sentence"])
        self.assertIn("covers/day", plan["sentence"])
        self.assertIn("90% range would move that line to", plan["sentence"])
        self.assertRegex(plan["sentence"], r"to [\d.]+ covers/day")
        memo = tenant.ensure_trial("# Memo\n## Limits\n- Taste.\n" + tenant.BACKTEST_NOTE, plan)
        self.assertIn(plan["sentence"], memo)
        self.assertTrue(memo.endswith(tenant.BACKTEST_NOTE))
        self.assertIn(plan["sentence"], tenant.template_memo("Sample Park", "Chicago", [], {}, trial_sentence=plan["sentence"]))
        self.assertIn("not a vetted address", plan["sentence"])
        self.assertEqual(plan["sha256"], ledger.trial_fingerprint(plan))
        outcome = trial.judge(plan, plan["threshold"], 4)
        self.assertTrue(outcome["cleared"])
        self.assertFalse(trial.judge(plan, plan["threshold"] - 1, 4)["cleared"])
        batch = ledger.load()["batches"][0]
        self.assertEqual(batch["sha256"], "f08f78d5b2a1c2c1b46c87429e30e1e1bf9c912450b19a2ade608891a31d3b46")
        self.assertEqual(ledger.fingerprint(batch), batch["sha256"])
        self.assertEqual(ledger.fingerprint({**batch, "trial": plan["sha256"]}), batch["sha256"])
        self.assertNotIn(plan["sha256"], ledger.canonical(batch))
        # The stated Chicago restaurant inputs are used above; keep the name so an unused import is not the point.
        self.assertEqual(defaults["currency"], "USD")

    def test_a_missing_ceiling_has_no_plan(self):
        concept = {"concept": "ramen shop", "rent_group": "restaurant", "call": "GO"}
        self.assertIsNone(trial.recommend("X", concept, "chicago", None, None, None, "2026-10-14T22:00:00Z"))

    def test_no_go_and_a_negative_line_and_another_concepts_voi_do_not_make_a_plan(self):
        concept = {"concept": "ramen shop", "rent_group": "restaurant", "call": "NO-GO"}
        rent_row = {"viable": True, "rent_max": 6000, "relative_demand": 1.0, "group": "restaurant"}
        self.assertIsNone(trial.recommend("X", concept, "chicago", rent_row, None, None, "2026-10-14T22:00:00Z"))
        concept = {"concept": "ramen shop", "rent_group": "restaurant", "call": "ADJUST"}
        huge = {"viable": True, "rent_max": 10_000_000, "relative_demand": 1.0, "group": "restaurant"}
        staff_voi = {"evpi": 100, "concept": "ramen shop", "checks": [
            {"input": "staff", "evppi": 80, "above_noise": True}]}
        self.assertIsNone(trial.recommend("X", concept, "chicago", huge, None, staff_voi, "2026-10-14T22:00:00Z"))
        staff = trial.threshold_for("staff", cb.defaults("chicago", "restaurant")["inputs"], 1.0, 10_000_000)
        self.assertEqual(staff[1], "at_most")
        self.assertLessEqual(staff[0], 0)
        other = {"evpi": 10, "concept": "bakery", "checks": [
            {"input": "ticket", "evppi": 9, "above_noise": True}]}
        plan = trial.recommend("X", concept, "chicago", rent_row, None, other, "2026-10-14T22:00:00Z")
        self.assertEqual(plan["check"], "covers_day")
        self.assertIsNone(trial.recommend("X", concept, "chicago", {"viable": True, "rent_max": "nope"}, None, None,
                                          "2026-10-14T22:00:00Z"))


if __name__ == "__main__":
    unittest.main()
