import math
import random
import unittest

import cityscan_math as cm


class Kernel(unittest.TestCase):
    def test_exponential_decay(self):
        self.assertEqual(cm.exp_kernel(0), 1.0)
        self.assertAlmostEqual(cm.exp_kernel(400, 400), math.exp(-1))
        self.assertAlmostEqual(cm.exp_kernel(400 * math.log(2), 400), 0.5)  # half-life = lambda ln 2
        self.assertGreater(cm.exp_kernel(100), cm.exp_kernel(200))

    def test_stencil_expectation(self):
        offs = {(dr, dc): k for dr, dc, k in cm.cell_kernel_offsets(400, 400)}
        self.assertLess(offs[(0, 0)], 1.0)  # a point anywhere in the cell, not at the centre
        self.assertGreater(offs[(0, 0)], math.exp(-0.5))
        self.assertGreater(offs[(0, 1)], offs[(0, 2)])
        self.assertGreater(offs[(0, 1)], offs[(1, 1)])
        self.assertNotIn((0, 4), offs)  # beyond the 3 lambda cutoff

    def test_density_index_matches_brute_force(self):
        rng = random.Random(1)
        pts = [(rng.uniform(0, 3000), rng.uniform(0, 3000), rng.uniform(0.5, 2)) for _ in range(400)]
        idx = cm.GridIndex(pts, 400)
        for x, y in [(1500, 1500), (10, 2900), (2500, 700)]:
            brute = sum(w * cm.exp_kernel(math.hypot(px - x, py - y)) for px, py, w in pts
                        if math.hypot(px - x, py - y) <= 1200)
            self.assertAlmostEqual(cm.kernel_density_at(idx, x, y), brute, places=9)

    def test_smooth_is_linear_and_local(self):
        offs = cm.cell_kernel_offsets(400, 400)
        v = [0.0] * 100
        v[55] = 2.0
        out = cm.smooth(v, 10, 10, offs)
        k0 = dict(((dr, dc), k) for dr, dc, k in offs)[(0, 0)]
        self.assertAlmostEqual(out[55], 2 * k0)
        self.assertEqual(out[0], 0.0)


class Huff(unittest.TestCase):
    def test_hand_computed_two_competitors(self):
        p_new, p_k = cm.huff_probabilities(100, [200, 400], alpha=1, beta=2)
        # utilities 1e-4, 2.5e-5, 6.25e-6 -> total 1.3125e-4
        self.assertAlmostEqual(p_new, 1e-4 / 1.3125e-4)
        self.assertAlmostEqual(p_k[0], 2.5e-5 / 1.3125e-4)
        self.assertAlmostEqual(p_k[1], 6.25e-6 / 1.3125e-4)
        self.assertAlmostEqual(p_new + sum(p_k), 1.0)

    def test_sum_to_one_and_floor(self):
        rng = random.Random(3)
        for _ in range(50):
            d = [rng.uniform(0, 3000) for _ in range(rng.randint(0, 6))]
            a = [rng.uniform(0.5, 3) for _ in d]
            p_new, p_k = cm.huff_probabilities(rng.uniform(0, 3000), d, 1.5, a, alpha=1, beta=2)
            self.assertAlmostEqual(p_new + sum(p_k), 1.0)
        self.assertEqual(cm.huff_probabilities(10, [50])[0], 0.5)  # 10 m is floored to 50 m

    def test_share_matches_full_form(self):
        p_new, _ = cm.huff_probabilities(300, [150, 600])
        s = 150 ** -2 + 600 ** -2
        self.assertAlmostEqual(cm.huff_share(300, s), p_new)
        self.assertEqual(cm.huff_share(300, 0.0), 1.0)

    def test_ripley_l_zero_under_csr_expectation(self):
        area, n, r = 4e6, 400, 300.0
        expected = n * math.pi * r * r / area
        self.assertAlmostEqual(cm.local_l_excess(expected, n, area, r), 0.0, places=9)
        self.assertGreater(cm.local_l_excess(4 * expected, n, area, r), 0)
        self.assertEqual(cm.local_l_excess(0, n, area, r), -r)


class Logistic(unittest.TestCase):
    def test_recovers_known_weights(self):
        rng = random.Random(42)
        true = [-0.5, 1.5, -1.0]
        X, y = [], []
        for _ in range(4000):
            x = [rng.gauss(0, 1), rng.gauss(0, 1)]
            X.append(x)
            y.append(1 if rng.random() < cm.sigmoid(cm.logit(true, x)) else 0)
        beta = cm.fit_logistic(X, y, l2=0.0)
        for b, t in zip(beta, true):
            self.assertAlmostEqual(b, t, delta=0.15)

    def test_l2_shrinks(self):
        rng = random.Random(5)
        X = [[rng.gauss(0, 1)] for _ in range(300)]
        y = [1 if x[0] + rng.gauss(0, 1) > 0 else 0 for x in X]
        free, ridge = cm.fit_logistic(X, y, l2=0.0), cm.fit_logistic(X, y, l2=200.0)
        self.assertLess(abs(ridge[1]), abs(free[1]))

    def test_linear_algebra(self):
        low = cm.cholesky([[4.0, 2.0], [2.0, 3.0]])
        self.assertAlmostEqual(low[0][0], 2)
        self.assertAlmostEqual(low[1][0], 1)
        self.assertAlmostEqual(low[1][1], math.sqrt(2))
        inv = cm.invert([[2.0, 1.0], [1.0, 3.0]])
        self.assertAlmostEqual(inv[0][0], 3 / 5)
        self.assertAlmostEqual(inv[0][1], -1 / 5)

    def test_laplace_standard_error_matches_theory(self):
        # one centred feature, beta = 0: SE(slope) = 1 / sqrt(n p (1 - p) var(x)) = 2 / sqrt(n)
        X = [[-1.0], [1.0]] * 500
        cov = cm.logistic_covariance(X, [1.0] * 1000, [0.0, 0.0], l2=0.0)
        self.assertAlmostEqual(math.sqrt(cov[1][1]), 2 / math.sqrt(1000), places=6)

    def test_standardize(self):
        mu, sd = cm.standardize([[1, 5], [3, 5]])
        self.assertEqual(mu, [2, 5])
        self.assertEqual(sd, [1, 1.0])


class Validation(unittest.TestCase):
    def test_auc_separable_and_random(self):
        self.assertEqual(cm.auc([3, 4, 5], [0, 1, 2]), 1.0)
        self.assertEqual(cm.auc([0, 1], [2, 3]), 0.0)
        self.assertEqual(cm.auc([1, 1], [1, 1]), 0.5)
        rng = random.Random(9)
        a = cm.auc([rng.random() for _ in range(2000)], [rng.random() for _ in range(2000)])
        self.assertAlmostEqual(a, 0.5, delta=0.03)

    def test_auc_matches_pair_count(self):
        rng = random.Random(2)
        pos = [rng.randint(0, 5) for _ in range(30)]
        neg = [rng.randint(0, 5) for _ in range(25)]
        pairs = sum(1.0 if p > q else 0.5 if p == q else 0.0 for p in pos for q in neg) / (30 * 25)
        self.assertAlmostEqual(cm.auc(pos, neg), pairs)

    def test_bootstrap_ci(self):
        rng = random.Random(4)
        pos = [rng.gauss(1, 1) for _ in range(200)]
        neg = [rng.gauss(0, 1) for _ in range(200)]
        lo, hi = cm.bootstrap_auc_ci(pos, neg, 200, seed=1)
        self.assertLess(lo, cm.auc(pos, neg))
        self.assertGreater(hi, cm.auc(pos, neg))
        self.assertEqual((lo, hi), cm.bootstrap_auc_ci(pos, neg, 200, seed=1))

    def test_block_folds_are_contiguous_bands(self):
        coord = [i % 20 for i in range(400)]
        folds = cm.block_folds(coord, 5)
        by_coord = {}
        for c, f in zip(coord, folds):
            by_coord.setdefault(c, set()).add(f)
        self.assertTrue(all(len(s) == 1 for s in by_coord.values()))  # a band never splits a coordinate
        seq = [by_coord[c].pop() for c in sorted(by_coord)]
        self.assertEqual(seq, sorted(seq))
        self.assertEqual(set(seq), set(range(5)))


class EmpiricalBayes(unittest.TestCase):
    counts = [2, 4, 6, 8, 10, 12, 14, 16, 18, 50, 0, 9, 11, 7]

    def test_shrinks_extremes_toward_mean(self):
        a, b = cm.eb_gamma_prior(self.counts)
        m = sum(self.counts) / len(self.counts)
        self.assertAlmostEqual(a / b, m)
        for c in (0, 50):
            shape, rate = cm.gamma_posterior(a, b, c)
            post = shape / rate
            self.assertTrue(min(c, m) < post < max(c, m), (c, post, m))
        lo, hi = cm.gamma_interval(*cm.gamma_posterior(a, b, 50))
        self.assertTrue(lo < (a + 50) / (b + 1) < hi)

    def test_no_overdispersion_pools_everything(self):
        a, b = cm.eb_gamma_prior([5, 5, 5, 5])
        self.assertAlmostEqual(cm.gamma_posterior(a, b, 0)[0] / cm.gamma_posterior(a, b, 0)[1], 5, places=3)

    def test_gamma_interval_large_shape_is_normal(self):
        lo, hi = cm.gamma_interval(400, 1)
        self.assertAlmostEqual(lo, 400 - 1.645 * 20, delta=1.5)
        self.assertAlmostEqual(hi, 400 + 1.645 * 20, delta=1.5)


class TwoSources(unittest.TestCase):
    def test_gap_and_completeness(self):
        self.assertEqual(cm.coverage_gap(3, 4), 0.25)
        self.assertEqual(cm.coverage_gap(5, 4), 0.0)
        self.assertEqual(cm.coverage_gap(0, 0), 0.0)
        self.assertEqual(cm.agreement(2, 8), 0.25)
        self.assertIsNone(cm.agreement(0, 0))
        self.assertAlmostEqual(cm.completeness([(2, 8), (5, 5), (0, 0)]), 0.625)
        self.assertIsNone(cm.completeness([(0, 0)]))

    def test_chapman_hand_case(self):
        ch = cm.chapman(100, 80, 40)  # (101 * 81) / 41 - 1
        self.assertAlmostEqual(ch["N"], 101 * 81 / 41 - 1)
        self.assertEqual(ch["union"], 140)
        self.assertAlmostEqual(ch["missed_by_both"], ch["N"] - 140)
        self.assertGreater(ch["sd"], 0)

    def test_chapman_recovers_population(self):
        rng = random.Random(8)
        est = []
        for _ in range(200):  # two independent surveys of N = 500 with detection 0.6 and 0.4
            a = {k for k in range(500) if rng.random() < 0.6}
            b = {k for k in range(500) if rng.random() < 0.4}
            est.append(cm.chapman(len(a), len(b), len(a & b))["N"])
        self.assertAlmostEqual(sum(est) / len(est), 500, delta=10)

    def test_matching_names_and_distance(self):
        a = [(0, 0, "cafe de paris"), (100, 0, "pizza roma"), (300, 0, ""), (600, 0, "atlas")]
        b = [(10, 5, "café de paris".replace("é", "e")), (130, 0, "pizza roma"), (305, 0, ""), (640, 0, "zara")]
        pairs = cm.match_points(a, b, max_d=50, min_sim=0.6)
        self.assertEqual(sorted(pairs), [(0, 0), (1, 1), (2, 2)])  # (3, 3) is close but a different name
        self.assertEqual(cm.match_points(a, [(1000, 0, "cafe de paris")]), [])  # same name, too far

    def test_name_containment(self):
        self.assertEqual(cm.name_similarity("nafoura النافورة", "nafoura"), 0.9)
        self.assertLess(cm.name_similarity("roma", "ab"), 0.6)
        self.assertLess(cm.name_similarity("le", "le rodeo"), 0.9)  # tokens shorter than 3 never "contain"

    def test_matching_is_one_to_one(self):
        a = [(0, 0, "atlas"), (5, 0, "atlas")]
        b = [(2, 0, "atlas")]
        self.assertEqual(len(cm.match_points(a, b)), 1)

    def test_detection_aware_eb(self):
        counts = [2, 4, 6, 8, 10, 12, 14, 16, 18, 50, 0, 9, 11, 7]
        a1, b1 = cm.eb_gamma_prior(counts, 1.0)
        a5, b5 = cm.eb_gamma_prior(counts, 0.5)
        self.assertAlmostEqual(a5 / b5, 2 * a1 / b1)  # half detected -> true mean doubles
        s, r = cm.gamma_posterior(a5, b5, 10, q=0.5)
        self.assertGreater(s / r, 10)  # an observed 10 at 50% detection means more than 10
        s1, r1 = cm.gamma_posterior(a1, b1, 10)
        sk, rk = cm.gamma_posterior(a1, b1, 10, kappa=0.25)
        self.assertAlmostEqual(s1 / r1, sk / rk)  # same mean ...
        self.assertAlmostEqual(sk / rk ** 2, 4 * s1 / r1 ** 2)  # ... four times the variance


class ConstrainedAndCalibrated(unittest.TestCase):
    def _data(self, true, n=3000, seed=4):
        rng = random.Random(seed)
        X = [[rng.gauss(0, 1) for _ in range(len(true) - 1)] for _ in range(n)]
        y = [1 if rng.random() < cm.sigmoid(cm.logit(true, x)) else 0 for x in X]
        return X, y

    def test_sign_constraint_pins_a_truly_negative_effect_at_zero(self):
        X, y = self._data([0.0, 1.0, -0.8])
        free = cm.fit_logistic(X, y)
        self.assertLess(free[2], -0.5)
        beta, pinned = cm.fit_logistic_nonneg(X, y, nonneg=[0, 1])
        self.assertEqual(pinned, {1})
        self.assertEqual(beta[2], 0.0)
        self.assertGreater(beta[1], 0.7)

    def test_constraint_inactive_when_effects_are_positive(self):
        X, y = self._data([0.0, 1.0, 0.5])
        free = cm.fit_logistic(X, y)
        beta, pinned = cm.fit_logistic_nonneg(X, y, nonneg=[0, 1])
        self.assertEqual(pinned, set())
        self.assertAlmostEqual(beta[1], free[1], places=6)

    def test_prior_centre_shrinks_toward_prior_not_zero(self):
        X, y = self._data([0.0, 0.0], n=40, seed=9)
        b = cm.fit_logistic(X, y, l2=50.0, prior=[2.0])
        self.assertGreater(b[1], 1.0)

    def test_platt_recovers_calibration(self):
        rng = random.Random(3)
        s = [rng.gauss(0, 2) for _ in range(4000)]
        y = [1 if rng.random() < cm.sigmoid(0.5 * v - 1.0) else 0 for v in s]
        a, b = cm.platt(s, y)
        self.assertAlmostEqual(a, 0.5, delta=0.08)
        self.assertAlmostEqual(b, -1.0, delta=0.12)

    def test_top_share_and_ties(self):
        y = [1, 0, 0, 0, 1, 0, 0, 0, 0, 0]
        s = [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
        self.assertEqual(cm.top_share(y, s, 0.2), 0.5)
        self.assertAlmostEqual(cm.top_share(y, [1] * 10, 0.2), 0.2)  # a constant score gets the base rate
        self.assertEqual(cm.top_share(y, s, k=5), 1.0)

    def test_paired_bootstrap_difference(self):
        rng = random.Random(5)
        y = [1 if rng.random() < 0.2 else 0 for _ in range(600)]
        good = [t + rng.gauss(0, 0.5) for t in y]
        noise = [rng.random() for _ in y]
        d, lo, hi = cm.paired_bootstrap_diff(y, good, noise, cm.auc_scores, 100, 1)
        self.assertGreater(lo, 0.2)
        d0, lo0, hi0 = cm.paired_bootstrap_diff(y, good, good, cm.auc_scores, 50, 1)
        self.assertEqual((d0, lo0, hi0), (0.0, 0.0, 0.0))


class Sobol(unittest.TestCase):
    def test_additive_function_shares(self):
        c = [1.0, 2.0, 3.0]  # Var(c_i U) = c_i^2 / 12, so S_i = c_i^2 / sum c^2 = 1/14, 4/14, 9/14
        samplers = [lambda rng: rng.random()] * 3
        s1, st, var = cm.sobol_indices(lambda x: sum(a * b for a, b in zip(c, x)), samplers, n=4000, seed=2)
        for got, want in zip(s1, [1 / 14, 4 / 14, 9 / 14]):
            self.assertAlmostEqual(got, want, delta=0.04)
        for got, want in zip(st, [1 / 14, 4 / 14, 9 / 14]):
            self.assertAlmostEqual(got, want, delta=0.04)
        self.assertAlmostEqual(var, 14 / 12, delta=0.1)

    def test_interaction_shows_in_total_not_first_order(self):
        samplers = [lambda rng: rng.uniform(-1, 1)] * 2
        s1, st, _ = cm.sobol_indices(lambda x: x[0] * x[1], samplers, n=4000, seed=3)
        self.assertLess(max(abs(v) for v in s1), 0.06)
        self.assertGreater(min(st), 0.9)


class SpatialAutocorrelation(unittest.TestCase):
    def grid(self, f, n=10):
        return {r * n + c: f(r, c) for r in range(n) for c in range(n)}

    def test_checkerboard_is_minus_one(self):
        m = cm.morans_i(self.grid(lambda r, c: (r + c) % 2), cm.rook_neighbors(10, 10))
        self.assertAlmostEqual(m["I"], -1.0)
        self.assertLess(m["z"], -5)

    def test_two_blocks_near_plus_one(self):
        m = cm.morans_i(self.grid(lambda r, c: 1.0 if c < 5 else 0.0), cm.rook_neighbors(10, 10))
        self.assertGreater(m["I"], 0.85)
        self.assertGreater(m["z"], 5)
        self.assertAlmostEqual(m["expected"], -1 / 99)

    def test_random_is_near_zero(self):
        rng = random.Random(11)
        m = cm.morans_i({i: rng.random() for i in range(400)}, cm.rook_neighbors(20, 20))
        self.assertLess(abs(m["z"]), 2.5)

    def test_gi_star_finds_hot_corner(self):
        vals = self.grid(lambda r, c: 10.0 if r < 3 and c < 3 else 1.0)
        z = cm.getis_ord_gi_star(vals, cm.queen_neighbors(10, 10, self_=True))
        self.assertGreater(z[1 * 10 + 1], 1.96)
        self.assertLess(z[8 * 10 + 8], 0)


if __name__ == "__main__":
    unittest.main()
