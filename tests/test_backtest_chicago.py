import unittest

import backtest_chicago as bt

C = bt.CONCEPTS


def row(**survived):
    r = {c: [0, 0] for c in C}
    for c, (o, s) in survived.items():
        r[c] = [o, s]
    return r


class Classify(unittest.TestCase):
    def test_keywords(self):
        self.assertEqual(bt.classify("Taqueria El Sol"), "taqueria")
        self.assertEqual(bt.classify("BONCHON WICKER PARK"), "Korean fried chicken")
        self.assertEqual(bt.classify("Pho 777"), "Vietnamese pho")
        self.assertEqual(bt.classify("Beirut Shawarma"), "Lebanese grill")
        self.assertEqual(bt.classify("CAVA"), "Mediterranean bowls")
        self.assertEqual(bt.classify("La Boulangerie"), "bakery")
        self.assertEqual(bt.classify("Dark Matter Coffee"), "specialty coffee")

    def test_no_false_matches(self):
        self.assertIsNone(bt.classify("Cavalier Grill"))
        self.assertIsNone(bt.classify("Phoenix Inn"))
        self.assertIsNone(bt.classify("Italian Kitchen"))
        self.assertIsNone(bt.classify(None))


class Register(unittest.TestCase):
    def test_openings_first_passing_license_in_window(self):
        rows = [
            {"license_": "1", "inspection_date": "2022-12-01T00:00:00", "results": "Pass", "dba_name": "A",
             "latitude": "41.9", "longitude": "-87.6"},
            {"license_": "1", "inspection_date": "2023-06-01T00:00:00", "results": "Pass", "dba_name": "A",
             "latitude": "41.9", "longitude": "-87.6"},
            {"license_": "4", "inspection_date": "2022-11-01T00:00:00", "results": "Fail", "dba_name": "D",
             "latitude": "41.9", "longitude": "-87.6"},
            {"license_": "4", "inspection_date": "2023-02-01T00:00:00", "results": "Pass", "dba_name": "D",
             "latitude": "41.9", "longitude": "-87.6"},
            {"license_": "2", "inspection_date": "2023-03-01T00:00:00", "results": "Fail", "dba_name": "B",
             "latitude": "41.9", "longitude": "-87.6"},
            {"license_": "2", "inspection_date": "2023-04-01T00:00:00", "results": "Pass w/ Conditions",
             "dba_name": "B", "aka_name": "B Tacos", "latitude": "41.9", "longitude": "-87.6"},
            {"license_": "3", "inspection_date": "2024-05-01T00:00:00", "results": "Pass", "dba_name": "C"},
        ]
        out = bt.openings(rows)
        self.assertEqual(sorted(out), ["2", "4"])  # 1 passed before the window: a renewal, not an opening
        self.assertEqual(out["2"]["opened"], "2023-04-01")
        self.assertEqual(out["2"]["name"], "B Tacos")

    def test_survived(self):
        self.assertTrue(bt.survived([("2023-04-01", "Pass"), ("2025-06-01", "Pass")], "2023-04-01"))
        self.assertFalse(bt.survived([("2023-04-01", "Pass"), ("2025-06-01", "Out of Business")], "2023-04-01"))
        self.assertFalse(bt.survived([("2023-04-01", "Pass"), ("2024-06-01", "Pass")], "2023-04-01"))
        self.assertTrue(bt.survived([("2021-01-01", "Out of Business"), ("2025-06-01", "Pass")], "2023-04-01"))

    def test_outcomes_radius_and_classification(self):
        opened = {"1": {"name": "Tacos Uno", "lat": 41.9088, "lon": -87.6796, "opened": "2023-05-01"},
                  "2": {"name": "Tacos Far", "lat": 41.80, "lon": -87.6796, "opened": "2023-05-01"},
                  "3": {"name": "Italian Place", "lat": 41.9088, "lon": -87.6796, "opened": "2023-05-01"}}
        t = bt.outcomes(opened, {"1": True, "2": True, "3": True}, areas=[("Wicker Park", 41.9088, -87.6796)])
        self.assertEqual(t["Wicker Park"]["taqueria"], [1, 1])
        self.assertEqual(sum(o for o, _ in t["Wicker Park"].values()), 1)

    def test_overlapping_areas_count_an_opening_once(self):
        opened = {"1": {"name": "Tacos Uno", "lat": 41.9100, "lon": -87.6796, "opened": "2023-05-01"}}
        areas = [("Near", 41.9088, -87.6796), ("Also within radius", 41.9180, -87.6796)]
        t = bt.outcomes(opened, {"1": True}, areas=areas)
        self.assertEqual((t["Near"]["taqueria"], t["Also within radius"]["taqueria"]), ([1, 1], [0, 0]))


class Metrics(unittest.TestCase):
    def test_hits_and_split(self):
        r = row(**{C[0]: (2, 2), C[5]: (3, 1)})
        self.assertEqual(bt.hits_at_k(C, r), (2, 3))
        self.assertEqual(bt.survival_split(C, r), ((2, 2), (1, 3)))

    def test_prior_leaves_area_out(self):
        table = {"A": row(**{C[9]: (5, 5)}), "B": row(**{C[4]: (1, 1)})}
        self.assertEqual(bt.prior_order(table, "A")[0], C[4])
        self.assertEqual(bt.prior_order(table, "B")[0], C[9])

    def test_spearman(self):
        perfect = row(**{c: (10 - i, 10 - i) for i, c in enumerate(C)})
        self.assertAlmostEqual(bt.spearman(C, perfect), 1.0)
        self.assertAlmostEqual(bt.spearman(list(reversed(C)), perfect), -1.0)
        self.assertIsNone(bt.spearman(C, row()))

    def test_common_areas_and_score(self):
        table = {"A": row(**{C[0]: (1, 1)}), "B": row(**{C[1]: (1, 1)}), "Z": row()}
        orders = {"qloo": {"A": C, "B": None, "Z": C}, "prior": {"A": list(reversed(C)), "B": C, "Z": C}}
        self.assertEqual(bt.common_areas(table, orders), ["A"])
        s = bt.score(table, orders)
        self.assertEqual(s["qloo"]["hit_at_k"], 1.0)
        self.assertEqual(s["prior"]["hit_at_k"], 0.0)
        self.assertEqual(s["qloo"]["areas"], 1)

    def test_llm_orders_retries_and_completes(self):
        replies = [RuntimeError("429"), {"content": '{"ranking": ["bakery", "taqueria", "ramen shop"]}', "provider": "x"},
                   RuntimeError("429"), RuntimeError("429"), RuntimeError("429")]

        def chat(_messages):
            r = replies.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        orders, provider = bt.llm_orders([("A", 0, 0), ("B", 0, 0)], chat, sleep=lambda s: None)
        self.assertEqual(orders["A"][:3], ["bakery", "taqueria", "ramen shop"])
        self.assertEqual(sorted(orders["A"]), sorted(C))
        self.assertIsNone(orders["B"])
        self.assertEqual(provider, "x")

    def test_tgi_orders(self):
        table = {"A": row(**{C[1]: (2, 2)}), "B": row(**{C[0]: (9, 9)})}
        d = {"A": {c: (1.0 if c == C[2] else 0.0) for c in C}}
        s = {"A": {c: ((0.5, 0.95) if c == C[3] else (1.0, 0.5)) for c in C}}
        o = bt.tgi_orders(table, [("A", 0, 0), ("B", 0, 0)], d, s)
        self.assertEqual(o["qloo_D"]["A"][0], C[2])
        self.assertEqual(o["osm_under"]["A"][0], C[3])
        self.assertEqual(set(o["tgi"]["A"][:2]), {C[2], C[3]})
        self.assertEqual(o["prior_tgi"]["A"][0], C[0])  # 9 survivors elsewhere outweigh D = 1 and log 2
        self.assertEqual(sorted(o["tgi"]["A"]), sorted(C))
        self.assertIsNone(o["tgi"]["B"])
        self.assertIsNone(o["qloo_D"]["B"])

    def test_qloo_orders_city_rank_and_rate_limit_retry(self):
        class Led:
            def __init__(self):
                self.calls, self.limited = [], 1

            def call(self, op, params, purpose):
                self.calls.append(params.get("signal_location") or op)
                if op == "find_tags":
                    return {"results": [{"id": f"urn:tag:{params['query']}", "name": params["query"],
                                         "type": "urn:tag:cuisine:qloo"}]}, {}
                if op == "recommend":
                    return {"results": [{"entity_id": "P" + params["include_tags"][0][-4:], "name": "Place"}]}, {}
                if params["signal_location"] == "A, Chicago" and self.limited:
                    self.limited -= 1
                    return None, {"error": "QLOO_RATE_LIMIT"}
                opts = params["options"] if params["signal_location"] != "Chicago" else list(reversed(params["options"]))
                return {"results": [{"entity_id": e, "affinity": 1 - i / 100} for i, e in enumerate(opts)]}, {}
        led = Led()
        orders, n, failures, d = bt.qloo_orders([("A", 0, 0)], led, city_rank=True, sleep=lambda s: None)
        self.assertEqual(led.calls[-3:], ["Chicago", "A, Chicago", "A, Chicago"])
        self.assertEqual(failures, {})
        self.assertTrue(orders["A"])
        self.assertTrue(d["A"])
        self.assertAlmostEqual(sum(d["A"].values()), 0.0)

    def test_bootstrap(self):
        lo, hi = bt.bootstrap_diff([(2, 1, 4), (3, 3, 5), (1, 0, 2)])
        self.assertLessEqual(lo, hi)
        self.assertIsNone(bt.bootstrap_diff([(0, 0, 0)]))


if __name__ == "__main__":
    unittest.main()
