"""Ground-truth gate on synthetic points (invented coordinates, no real register)."""
import unittest

import cityscan_groundtruth as gt
import cityscan_publish as pub

INFO = {"bbox": [10.0, 20.0, 10.1, 20.1], "center": [10.05, 20.05]}


def pack(points, overture=()):
    return {"info": INFO, "elements": [{"type": "node", "lat": a, "lon": b, "tags": {"amenity": "restaurant"}}
                                       for a, b in points],
            "overture": {"places": [[a, b, "x", "restaurant", 0.9] for a, b in overture]}}


SPARSE = [(10.01 + 0.007 * i, 20.01 + 0.007 * j) for i in range(10) for j in range(10)]


class GroundTruth(unittest.TestCase):
    def test_full_coverage_in_a_sparse_city_scores_near_one(self):
        r = gt.ground_truth(pack(SPARSE), SPARSE)
        self.assertEqual(r["recall"], 1.0)
        self.assertLess(r["placebo"], 0.1)
        self.assertGreater(r["kappa"], 0.9)

    def test_overture_counts_for_recall(self):
        r = gt.ground_truth(pack([], overture=SPARSE[:50]), SPARSE)
        self.assertEqual(r["recall"], 0.5)

    def test_dense_chance_matches_are_discounted(self):
        dense = [(10.03 + 0.0003 * i, 20.03 + 0.0003 * j) for i in range(60) for j in range(60)]
        mapped = dense[::7]
        unmapped = [(10.065 + 0.00002 * i, 20.07) for i in range(len(mapped))]  # an unmapped block, 2 km away
        r = gt.ground_truth(pack(dense), mapped + unmapped)
        self.assertEqual(r["recall"], 0.5)
        self.assertGreater(r["placebo"], 0.3)
        self.assertLess(r["kappa"], 0.35)

    def test_points_outside_the_pack_area_are_ignored(self):
        self.assertEqual(gt.ground_truth(pack(SPARSE), SPARSE + [(50.0, 50.0)])["n_official"], len(SPARSE))
        with self.assertRaises(ValueError):
            gt.ground_truth(pack(SPARSE), [(50.0, 50.0)])


class GateBasis(unittest.TestCase):
    CITY = {"coverage": {"city": {"data_confidence_food": 0.33}}}

    def test_official_register_overrides_two_source_agreement(self):
        q = pub.quality(self.CITY, {"kappa": 0.88}, "register")
        self.assertEqual((q["gate_passed"], q["basis"]), (True, "official register"))

    def test_low_register_recall_fails_with_a_reason(self):
        q = pub.quality(self.CITY, {"kappa": 0.60}, "register")
        self.assertFalse(q["gate_passed"])
        self.assertIn("officially registered", q["reason"])

    def test_without_register_agreement_is_the_fallback(self):
        q = pub.quality(self.CITY)
        self.assertEqual((q["gate_passed"], q["basis"]), (False, "two-source agreement"))
        self.assertIn("33%", q["reason"])


if __name__ == "__main__":
    unittest.main()
