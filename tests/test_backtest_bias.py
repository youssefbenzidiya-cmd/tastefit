"""Synthetic cells only. No Chicago download and no Qloo call."""
import json
import unittest
from pathlib import Path

import backtest_bias as bias
import backtest_chicago as bt

C = bt.CONCEPTS


def row(**survived):
    r = {c: [0, 0] for c in C}
    for c, (opened, survived_n) in survived.items():
        r[c] = [opened, survived_n]
    return r


class Heckman(unittest.TestCase):
    def test_the_selection_equation_is_not_identified(self):
        status = bias.heckman()
        self.assertFalse(status["identified"])
        self.assertIn("Heckman (1979)", status["reason"])
        self.assertIn("exclusion restriction", status["reason"])


class InverseProbability(unittest.TestCase):
    def test_weights_turn_survivors_back_into_openings_when_every_cell_has_a_survivor(self):
        # Two openings of the top concept survived once each is not the case: 4 opened, 1 survived.
        # A second concept opened 2 and both survived, and it is outside the top 3.
        r = row(**{C[0]: (4, 1), C[5]: (2, 2)})
        top, total, dropped = bias.ipw_hit(C, r, k=3)
        self.assertEqual(dropped, 0)
        self.assertEqual((top, total), (4.0, 6.0))
        self.assertEqual(bt.hits_at_k(C, r), (1, 3))

    def test_a_cell_with_no_survivor_is_dropped_rather_than_invented(self):
        r = row(**{C[0]: (3, 0), C[5]: (2, 2)})
        top, total, dropped = bias.ipw_hit(C, r, k=3)
        self.assertEqual(dropped, 3)
        self.assertEqual((top, total), (0.0, 2.0))

    def test_score_reports_the_ipw_hit_rate(self):
        table = {"A": row(**{C[0]: (4, 1), C[5]: (2, 2)})}
        orders = {"prior": {"A": C}}
        scored = bias.add_ipw(bt.score(table, orders), table, orders, ["A"])
        self.assertEqual(scored["prior"]["hit_at_k"], round(1 / 3, 3))
        self.assertEqual(scored["prior"]["ipw_hit_at_k"], round(4 / 6, 3))
        self.assertEqual(scored["prior"]["dropped_for_positivity"], 0)


class Conformal(unittest.TestCase):
    def test_a_perfect_score_has_a_zero_width_interval_and_full_coverage(self):
        pairs = [(i / 20, i / 20) for i in range(20)]
        out = bias.split_conformal(pairs, alpha=0.1, seed=1)
        self.assertEqual(out["quantile"], 0.0)
        self.assertEqual(out["coverage"], 1.0)
        self.assertEqual(out["mean_width"], 0.0)
        self.assertIn("Vovk", out["note"])

    def test_the_quantile_rank_is_the_finite_sample_one(self):
        # 18 pairs, calibration half 9. Rank = ceil(10 * 0.9) = 9, the largest residual.
        pairs = [(0.0, 0.5)] * 18
        out = bias.split_conformal(pairs, alpha=0.1, seed=1)
        self.assertEqual(out["n_cal"], 9)
        self.assertEqual(out["quantile"], 0.5)
        self.assertEqual(out["mean_width"], 1.0)

    def test_a_short_calibration_half_does_not_invent_a_finite_interval(self):
        # Calibration half is 5. Rank = ceil(6 * 0.9) = 6, past the sample.
        out = bias.split_conformal([(0.0, 1.0)] * 10, alpha=0.1, seed=1)
        self.assertIsNone(out["quantile"])
        self.assertIn("too small", out["note"])

    def test_prior_pairs_leave_the_area_out_of_its_score(self):
        table = {
            "A": row(**{C[0]: (2, 2)}),
            "B": row(**{C[0]: (2, 0), C[1]: (2, 2)}),
        }
        pairs = [(round(s, 3), round(y, 3)) for s, y in bias.prior_pairs(table, C)]
        # Area A is scored from B only. B's survivors are all C[1], so C[0]'s share in A is 0.
        self.assertIn((0.0, 1.0), pairs)


class PublishedPull(unittest.TestCase):
    def test_the_committed_pull_does_not_claim_a_tighter_interval(self):
        doc = json.loads((Path(__file__).parent.parent / "backtest_bias.json").read_text())
        self.assertEqual(doc["qloo_calls"], 0)
        self.assertFalse(doc["heckman"]["identified"])
        self.assertIn("Heckman (1979)", doc["heckman"]["reason"])
        prior = doc["methods"]["prior"]
        self.assertGreater(prior["ipw_hit_at_k"], prior["hit_at_k"])
        self.assertEqual(doc["conformal"]["quantile"], 1.0)
        self.assertGreaterEqual(doc["conformal"]["mean_width"], 1.0)
        self.assertNotIn("dba_name", json.dumps(doc))


if __name__ == "__main__":
    unittest.main()
