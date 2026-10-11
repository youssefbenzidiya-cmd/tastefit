import json
import random
import unittest

import cityscan as cs

BBOX = (0.0, 0.0, 0.05, 0.05)  # about 5.5 km square near (0, 0)


class Grid(unittest.TestCase):
    def test_dimensions_and_lookup(self):
        g = cs.make_grid(BBOX, cell_m=700)
        self.assertEqual((g["rows"], g["cols"]), (8, 8))
        self.assertEqual(cs.cell_of(g, 0.0001, 0.0001), 0)
        self.assertEqual(cs.cell_of(g, 0.0499, 0.0499), 63)
        self.assertIsNone(cs.cell_of(g, 0.06, 0.01))
        (s, w), (n, e) = cs.cell_bounds(g, 9)
        lat, lon = cs.cell_center(g, 9)
        self.assertTrue(s < lat < n and w < lon < e)
        self.assertEqual(cs.cell_of(g, lat, lon), 9)

    def test_neighbors(self):
        g = cs.make_grid(BBOX, cell_m=700)
        self.assertEqual(len(cs.neighbors(g, 0)), 3)
        self.assertEqual(len(cs.neighbors(g, 9)), 8)

    def test_cell_cap(self):
        g = cs.make_grid((0.0, 0.0, 0.4, 0.4))
        self.assertLessEqual(g["rows"] * g["cols"], 2500)
        self.assertGreater(g["cell_m"], 400)

    def test_clip_bbox_to_max_extent(self):
        s, w, n, e = cs.clip_bbox((29.0, -99.0, 31.0, -96.0), (30.27, -97.74), max_km=20)
        self.assertAlmostEqual((n - s) * cs.M_PER_DEG_LAT / 1000, 20, delta=0.1)
        self.assertLess(e - w, 0.25)

    def test_empty_bbox_rejected(self):
        with self.assertRaises(ValueError):
            cs.make_grid((1.0, 1.0, 1.0, 2.0))

    def test_area_shares_one_cell(self):
        g = cs.make_grid(BBOX, cell_m=700)
        (s, w), (n, e) = cs.cell_bounds(g, 0)
        shares = cs._area_shares(g, {"minlat": s, "minlon": w, "maxlat": n, "maxlon": e})
        self.assertEqual(list(shares), [0])
        self.assertAlmostEqual(shares[0], 49, delta=0.5)


class ConceptMapping(unittest.TestCase):
    def keys(self, text):
        return [g["key"] for g in cs.map_concept(text)["groups"]]

    def test_languages(self):
        self.assertEqual(self.keys("Korean fried chicken"), ["korean", "chicken"])
        self.assertEqual(cs.map_concept("Korean fried chicken")["profile"], "fast_casual")
        self.assertEqual(self.keys("Café de spécialité"), ["coffee"])
        self.assertEqual(cs.map_concept("مطعم مغربي فاخر")["profile"], "fine_dining")
        self.assertEqual(self.keys("pollo frito coreano"), ["korean", "chicken"])
        self.assertEqual(self.keys("comida vegana"), ["vegan"])

    def test_formats_and_fallback(self):
        self.assertEqual(cs.map_concept("Moroccan fine dining")["profile"], "fine_dining")
        self.assertEqual(self.keys("vegan bowls"), ["vegan", "bowls"])
        self.assertEqual(self.keys("sushi bar"), ["japanese"])
        self.assertEqual(self.keys("natural wine bar"), ["bar"])
        self.assertFalse(cs.map_concept("zzz concept")["recognized"])

    def test_competitor_weight(self):
        kfc = cs.map_concept("Korean fried chicken")
        self.assertEqual(cs.competitor_weight({"amenity": "restaurant", "cuisine": "korean;chicken"}, kfc), 1.0)
        self.assertEqual(cs.competitor_weight({"amenity": "restaurant", "cuisine": "korean"}, kfc), 0.5)
        self.assertEqual(cs.competitor_weight({"shop": "bakery", "cuisine": "korean"}, kfc), 0.0)
        vegan = cs.map_concept("vegan")
        self.assertEqual(cs.competitor_weight({"amenity": "restaurant", "diet:vegan": "yes"}, vegan), 0.5)

    def test_classify(self):
        self.assertEqual(cs.classify({"railway": "station", "public_transport": "station"}), ["station"])
        self.assertEqual(cs.classify({"office": "coworking"}), ["coworking"])
        self.assertEqual(cs.classify({"amenity": "cafe"}), [])


def synthetic_city():
    """Two office districts with cafés, homes in between, buses everywhere, three sushi bars."""
    rng = random.Random(123)
    els = []

    def add(n, lat, lon, spread, **tags):
        for _ in range(n):
            els.append({"lat": lat + rng.gauss(0, spread), "lon": lon + rng.gauss(0, spread), "tags": dict(tags)})

    def uniform(n, **tags):
        for _ in range(n):
            els.append({"lat": rng.uniform(0.001, 0.039), "lon": rng.uniform(0.001, 0.039), "tags": dict(tags)})

    for lat, lon in ((0.030, 0.031), (0.010, 0.008)):
        add(40, lat, lon, 0.0025, office="company")
        add(2, lat, lon, 0.001, amenity="university")
        add(2, lat, lon, 0.001, railway="station")
        add(22, lat, lon, 0.0018, amenity="cafe", name="Cafe")
    uniform(4, amenity="cafe", name="Corner cafe")
    uniform(60, highway="bus_stop")
    uniform(40, amenity="restaurant", cuisine="pizza")
    uniform(8, amenity="school")
    add(3, 0.02, 0.02, 0.004, amenity="restaurant", cuisine="sushi", name="Sushi")
    els.append({"bounds": {"minlat": 0.015, "minlon": 0.015, "maxlat": 0.025, "maxlon": 0.025},
                "tags": {"landuse": "residential"}})
    els.append({"lat": 0.030, "lon": 0.031, "tags": {"place": "neighbourhood", "name": "Business Bay"}})
    els.append({"lat": 0.010, "lon": 0.008, "tags": {"place": "suburb", "name": "Gare Sud"}})
    info = {"query": "Synthville", "name": "Synthville", "display_name": "Synthville", "center": [0.02, 0.02],
            "bbox": [0.0, 0.0, 0.04, 0.04]}
    return cs.build_city(info, els)


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.city = synthetic_city()
        cls.res, cls.st = cs.analyze(cls.city, "specialty coffee", top=5, cv=True, pool=25, draws=30)

    def test_low_completeness_widens_interval_and_lowers_lcb(self):
        # Full suitability x Huff formula: suitability-only intervals are saturated in this small synthetic city.
        opts = {"demand": True, "kernel_demand": True, "kernel_features": True}
        base, _ = cs.analyze(self.city, "specialty coffee", top=5, pool=25, draws=30, opts=opts)
        thin = dict(self.city, kappa=[cs.KAPPA_MIN] * len(self.city["kappa"]))
        res, _ = cs.analyze(thin, "specialty coffee", top=5, pool=25, draws=30, opts=opts)
        by_id = {t["id"]: t for t in res["top"]}
        cells = [t for t in base["top"] if t["id"] in by_id]
        self.assertTrue(cells)
        for t in cells:
            u = by_id[t["id"]]
            self.assertGreater(u["confidence_width"], t["confidence_width"])
            self.assertLess(u["opportunity"]["lcb"], t["opportunity"]["lcb"])

    def test_multi_part_concept_presences_match_every_part(self):
        res, _ = cs.analyze(self.city, "bakery café", top=0, pool=0, draws=0)
        c = cs.map_concept("bakery café")
        full = sum(1 for v in self.city["food"] if cs.competitor_weight(v["tags"], c) >= 1.0)
        partial = sum(1 for v in self.city["food"] if 0.5 <= cs.competitor_weight(v["tags"], c) < 1.0)
        self.assertGreater(partial, 0)
        self.assertEqual(res["model"]["n_presences"], full)
        self.assertIn("every part", res["model"]["presence_note"])

    def test_score_is_suitability_and_demand_is_display_only(self):
        self.assertFalse(self.res["params"]["options"]["demand"])
        for t in self.res["top"]:
            self.assertAlmostEqual(t["opportunity"]["point"], 100 * t["suitability"], delta=0.1)
        self.assertTrue(any(t["relative_demand"] > 0 for t in self.res["top"]))
        full, _ = cs.analyze(self.city, "specialty coffee", top=5, pool=25, draws=0,
                             opts={"demand": True, "kernel_demand": True, "kernel_features": True, "agg": True})
        self.assertTrue(any(abs(t["opportunity"]["point"] - 100 * t["suitability"]) > 1 for t in full["top"]))

    def test_city_build(self):
        g = self.city["grid"]
        self.assertEqual((g["rows"], g["cols"]), (12, 12))  # 4.42 km / 400 m, rounded up
        self.assertGreater(len(self.city["study"]), 50)
        self.assertEqual(sum(self.city["counts"]["office"]), 80)

    def test_model_is_learned_and_sensible(self):
        m = self.res["model"]
        self.assertTrue(m["fitted"])
        self.assertEqual(m["n_presences"], 48)
        self.assertGreater(m["coefficients"]["office"], 0)
        v = m["validation"]
        self.assertGreater(v["auc_cv"], 0.7)
        self.assertLessEqual(v["auc_ci95"][0], v["auc_cv"])
        self.assertGreater(v["top20_capture"], 0.2)  # better than a random 20% of cells

    def test_ranked_by_point_estimate_among_eligible_cells_with_interval(self):
        top = self.res["top"]
        score = [t["opportunity"]["rank_score"] for t in top]
        self.assertEqual(score, sorted(score, reverse=True))
        p_med = sorted(self.st["p"].values())[len(self.st["p"]) // 2]
        for t in top:
            o = t["opportunity"]
            self.assertTrue(t["eligible"])
            self.assertEqual(o["rank_score"], o["point"])
            self.assertGreaterEqual(t["suitability"], round(p_med, 3) - 0.001)
            self.assertLessEqual(o["lcb"], o["q20"])
            self.assertLessEqual(o["q20"], o["median"])
            self.assertLessEqual(o["median"], o["ucb"])
            self.assertTrue(0 <= t["huff_share"] <= 1)

    def test_quantile_ranking_option(self):
        res, _ = cs.analyze(self.city, "specialty coffee", top=5, pool=25, draws=30, opts={"rank": "q"})
        q20 = [t["opportunity"]["rank_score"] for t in res["top"]]
        self.assertEqual(q20, sorted(q20, reverse=True))
        self.assertEqual(q20, [t["opportunity"]["q20"] for t in res["top"]])

    def test_sign_constraints(self):
        m = self.res["model"]
        for f, b in m["coefficients"].items():
            if f != "food_other":
                self.assertGreaterEqual(b, 0.0, f)
        for f in m["constrained_at_zero"]:
            self.assertEqual(m["coefficient_se"][f], 0.0)

    def test_logit_decomposition_is_exact(self):
        for t in self.res["top"]:
            total = self.res["model"]["intercept"] + sum(t["logit_contributions"].values())
            self.assertAlmostEqual(total, t["logit"], delta=0.02)
            self.assertTrue(all("within 600 m" in r for r in t["reasons_en"]))
            self.assertTrue(all("à moins de 600 m" in r for r in t["reasons_fr"]))
            # offices, universities and stations share the same synthetic districts: no single coefficient is
            # identified, so the reasons are withheld rather than shown with a CI that crosses 0
            self.assertTrue(t["reasons_en"] or t["reasons_hidden_ci_crosses_0"] > 0)

    def test_reasons_need_a_coefficient_ci_excluding_zero(self):
        m = self.res["model"]
        t = self.res["top"][0]
        shown = {f for f in m["coefficients"] for r in t["reasons_en"] if cs.LABELS[f][0] in r}
        for f in shown:
            self.assertGreater(abs(m["coefficients"][f]) - 1.645 * m["coefficient_se"][f], 0)

    def test_diagnostics_present(self):
        d = self.res["diagnostics"]
        self.assertTrue(-1.5 < d["moran_residuals"]["I"] < 1.5)
        self.assertIn("hot_cells_z_gt_1.96", d["gi_star"])

    def test_deterministic_and_serialisable(self):
        again, _ = cs.analyze(self.city, "specialty coffee", top=5, cv=False, pool=25, draws=30)
        self.assertEqual([t["id"] for t in again["top"]], [t["id"] for t in self.res["top"]])
        self.assertEqual([t["opportunity"]["lcb"] for t in again["top"]],
                         [t["opportunity"]["lcb"] for t in self.res["top"]])
        doc = json.loads(json.dumps(self.res))
        self.assertIn("OpenStreetMap", doc["attribution"])

    def test_fallback_prior_when_few_presences(self):
        res, _ = cs.analyze(self.city, "sushi", top=3, pool=10, draws=10)
        self.assertFalse(res["model"]["fitted"])
        self.assertIn("prior", res["model"]["note"])

    def test_unit_to_concepts(self):
        pl = cs.find_place(self.city, "business bay")
        self.assertEqual(pl["name"], "Business Bay")
        card = cs.unit_concepts(self.city, pl["lat"], pl["lon"], ["specialty coffee", "sushi"], draws=10)
        self.assertEqual(len(card["concepts"]), 2)
        self.assertEqual([c["tier"] for c in card["concepts"]], ["ranked", "insufficient data"])
        coffee = next(c for c in card["concepts"] if c["concept"] == "specialty coffee")
        self.assertEqual(len(coffee["nearest_competitors"]), 3)
        self.assertTrue(0 <= coffee["city_percentile"] <= 100)
        self.assertTrue(0 < coffee["calibrated_probability"] < 1)
        sushi = next(c for c in card["concepts"] if c["concept"] == "sushi")
        self.assertIsNone(sushi["breakeven"])  # no ranking, no scenario, for "insufficient data"

    def test_backtest_row(self):
        row = cs.backtest_row(self.res)
        self.assertEqual(row["n_presences"], 48)
        self.assertIsNotNone(row["auc_cv"])


class Overture(unittest.TestCase):
    def test_category_mapping(self):
        self.assertEqual(cs.overture_tags("korean_restaurant"), {"amenity": "restaurant", "cuisine": "korean"})
        self.assertEqual(cs.overture_tags("coffee_shop")["amenity"], "cafe")
        self.assertEqual(cs.overture_tags("fast_food_restaurant"), {"amenity": "fast_food"})
        self.assertEqual(cs.overture_tags("college_university"), {"amenity": "university"})
        self.assertEqual(cs.overture_tags("corporate_or_business_office"), {"office": "company"})
        self.assertEqual(cs.overture_tags("train_station"), {"railway": "station"})
        for no in ("gas_station", "police_station", "doctors_office", "driving_school", "office_supply_store"):
            self.assertIsNone(cs.overture_tags(no), no)

    def test_specialty_cues(self):
        c = cs.map_concept("specialty coffee")
        self.assertIsNotNone(c["cues"])
        self.assertIsNone(cs.map_concept("coffee shop")["cues"])
        self.assertTrue(cs.matches_cues({"amenity": "cafe", "name": "Bondi Coffee Kitchen"}, c["cues"]))
        self.assertTrue(cs.matches_cues({"amenity": "cafe", "name": "ستاربكس", "brand": "Starbucks"}, c["cues"]))
        self.assertTrue(cs.matches_cues({"amenity": "cafe", "cuisine": "coffee_roastery"}, c["cues"]))
        self.assertFalse(cs.matches_cues({"amenity": "cafe", "name": "Café Atlas"}, c["cues"]))

    def test_name_key_drops_generic_words(self):
        self.assertEqual(cs._name_key("Café Leopardi"), "leopardi")
        self.assertEqual(cs._name_key("La Fabbrica Cafe Restaurant and Patisserie"), "fabbrica")
        self.assertEqual(cs._name_key("Café"), "")

    def test_overture_competitors_match_concepts(self):
        kfc = cs.map_concept("Korean fried chicken")
        self.assertEqual(cs.competitor_weight(cs.overture_tags("korean_restaurant"), kfc), 0.5)
        self.assertEqual(cs.competitor_weight(cs.overture_tags("chicken_restaurant"), kfc), 0.5)

    def test_union_and_completeness(self):
        rng = random.Random(5)
        info = {"query": "T", "name": "T", "display_name": "T", "center": [0.02, 0.02], "bbox": [0, 0, 0.04, 0.04]}
        els, ov = [], []
        for _ in range(30):  # west: both sources agree; east: Overture sees 3x more cafes than OSM
            lat, lon = rng.uniform(0.005, 0.035), rng.uniform(0.002, 0.018)
            nm = f"cafe {rng.randrange(10 ** 6)}"
            els.append({"lat": lat, "lon": lon, "tags": {"amenity": "cafe", "name": nm}})
            ov.append([lat + 0.00005, lon, nm, "cafe", 0.9])
        for k in range(30):
            lat, lon = rng.uniform(0.005, 0.035), rng.uniform(0.022, 0.038)
            nm = f"east {k}"
            if k % 3 == 0:
                els.append({"lat": lat, "lon": lon, "tags": {"amenity": "cafe", "name": nm}})
            ov.append([lat, lon, nm, "coffee_shop", 0.9])
        for _ in range(40):
            els.append({"lat": rng.uniform(0.001, 0.039), "lon": rng.uniform(0.001, 0.039),
                        "tags": {"highway": "bus_stop"}})
        city = cs.build_city(info, els, overture={"places": ov})
        cov = city["coverage"]
        self.assertEqual(cov["city"]["matched"], 40)  # 30 west + 10 east
        self.assertEqual(cov["city"]["union_pois"], 40 + 40 + 20)  # OSM (40 cafes + 40 stops) + 20 unmatched
        ch = cov["chapman"]["cafe"]
        self.assertEqual((ch["osm"], ch["overture"], ch["matched"]), (40, 60, 40))
        west = [cov["completeness"][i] for i in city["study"] if city["xy"][i][0] < -500
                and cov["completeness"][i] is not None]
        east = [cov["completeness"][i] for i in city["study"] if city["xy"][i][0] > 500
                and cov["completeness"][i] is not None]
        self.assertGreater(sum(west) / len(west), sum(east) / len(east))
        self.assertLess(min(city["kappa"]), 1.0)
        osm_only = cs.build_city(info, els)
        self.assertIsNone(osm_only["coverage"])
        self.assertEqual(sum(1 for v in city["food"]), 60)  # de-duplicated union of cafes


class Pack(unittest.TestCase):
    def test_pack_keeps_only_model_tags(self):
        pack = cs.make_pack({"name": "X"}, [
            {"type": "node", "lat": 1.0, "lon": 2.0, "tags": {"amenity": "cafe", "phone": "+1", "name": "A"}},
            {"type": "way", "center": {"lat": 1.0, "lon": 2.0}, "tags": {"office": "company"}},
            {"type": "node", "lat": 1.0, "lon": 2.0, "tags": {"phone": "+1"}},
        ])
        self.assertEqual(len(pack["elements"]), 2)
        self.assertNotIn("phone", pack["elements"][0]["tags"])

    def test_single_bounded_query(self):
        q = cs.overpass_query((33.5, -7.7, 33.6, -7.5), timeout=90)
        self.assertIn("[bbox:33.5,-7.7,33.6,-7.5]", q)
        self.assertIn("[timeout:90]", q)


if __name__ == "__main__":
    unittest.main()
