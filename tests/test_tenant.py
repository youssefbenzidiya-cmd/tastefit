"""Tenant-match pipeline on synthetic harness results: no Qloo, LLM or Nominatim calls."""
import datetime
import json
import unittest
from unittest import mock

import qloo
import tenant


def place(eid, name, affinity=0.9, description="Counter-serve spot", address="1 Sample St"):
    return {"entity_id": eid, "name": name, "affinity": affinity,
            "properties": {"short_description": description, "address": address, "price_level": 2}}


def ok(**kw):
    return {"status": "ok", **kw}


AREA = [place("A1", "Sample Tacos"), place("A2", "Sample Gallery"), place("A3", "Sample Records")]
BENCH = {"urn:tag:t:ramen": "B1", "urn:tag:t:coffee": "B2", "urn:tag:t:salad": "B3", "urn:tag:t:pho": "B4",
         "urn:tag:t:kfc": "B5", "urn:tag:t:bowls": "B6"}
LOCAL = {"urn:tag:t:ramen": 0, "urn:tag:t:coffee": 9, "urn:tag:t:salad": 2, "urn:tag:t:pho": 5}
TAGS = {"ramen": "urn:tag:t:ramen", "coffee": "urn:tag:t:coffee", "salad": "urn:tag:t:salad", "pho": "urn:tag:t:pho",
        "chicken": "urn:tag:t:kfc", "bowls": "urn:tag:t:bowls"}


class FakeQloo:
    """Routes (operation, params) to synthetic results and records every call."""

    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, op, params, cache=None, timeout=90):
        self.calls.append((op, params))
        if op in self.fail:
            return {"status": "error", "error": {"code": "QLOO_UPSTREAM"}}, False
        if op == "recommend" and not params.get("include_tags"):
            return ok(results=AREA), False
        if op == "recommend":
            tag = params["include_tags"][0]
            if params["filter_location"] == params["signal_location"]:
                n = LOCAL.get(tag, 0)
                return ok(results=[place(f"L{tag[-3:]}{i}", f"Local {tag[-5:]} {i}") for i in range(n)]), False
            return ok(results=[place("X0", "Sample Wine Bar", description="Wine bar with small plates"),
                               place(BENCH[tag], f"Bench {tag.split(':')[-1]}")]), False
        if op == "entity_tags":
            return ok(results=[{"id": "urn:tag:credit_card:place:visa", "name": "Visa"},
                               {"id": "urn:tag:genre:music:indie", "name": "Indie", "type": "urn:tag:genre:music"},
                               {"id": "urn:tag:cuisine:qloo:tacos", "name": "Tacos", "type": "urn:tag:cuisine:qloo"},
                               {"id": "urn:tag:genre:brand:wine_and_spirits:beer", "name": "Beer"}]), False
        if op == "audience_demographics":
            return ok(results=[{"query": {"age": {"25_to_29": 0.12, "55_and_older": -0.3}, "gender": {"female": 0.04}}}]), False
        if op == "find_tags":
            key = next((k for k in TAGS if k in params["query"].lower()), None)
            if key is None:
                return ok(results=[]), False
            return ok(results=[{"id": "urn:tag:genre:media:x", "name": "Media", "type": "urn:tag:genre:media"},
                               {"id": TAGS[key], "name": key.title(), "type": "urn:tag:cuisine:qloo"}]), False
        if op == "rank":
            order = ["B1", "B3", "B2", "B4", "B5", "B6"]
            if params["signal_location"] == "Chicago":  # the city-wide ranking for the over-index
                order = ["B2", "B4", "B1", "B3", "B5", "B6"]
            return ok(results=[{"entity_id": e, "affinity": 0.999 - i * 0.0001} for i, e in enumerate(order)
                               if e in params["options"]]), False
        if op == "compare_audiences":
            return ok(results={"tags": [{"name": "Place", "query": {"score": 0.99}},
                                        {"name": "Umami", "query": {"score": 0.8}},
                                        {"name": "Casual", "query": {"score": 0.6}}]}), False
        if op == "where_popular":
            return ok(results=[{"location": {"latitude": 41.91, "longitude": -87.68}, "query": {"affinity": 1}}]), False
        if op == "trends":
            if params["entities"] == ["Ambiguous Chain"]:
                return {"status": "needs_input", "resolution": {"issues": [{"candidates": [{"id": "BR1", "name": "Chain One"}]}]}}, False
            return ok(series=[{"entity": {"name": "Chain One"}, "points": [
                {"date": "2026-10-01", "population_percentile": 0.62}, {"date": "2026-05-01", "population_percentile": 0.6}]}]), False
        raise AssertionError(f"unexpected {op}")


CANDIDATES = {"candidates": [
    {"concept": "Ramen counter", "tag_query": "ramen", "rent_group": "restaurant", "reference_chain": "Ambiguous Chain", "why": "w"},
    {"concept": "Craft beer taproom", "tag_query": "beer", "rent_group": "other", "why": "excluded"},
    {"concept": "Specialty coffee", "tag_query": "coffee", "rent_group": "cafe", "why": "w"},
    {"concept": "Salad bar", "tag_query": "salad", "rent_group": "fast_food", "why": "food bar stays"},
    {"concept": "Pho house", "tag_query": "pho", "rent_group": "restaurant", "why": "w"},
    {"concept": "Fried chicken", "tag_query": "chicken", "rent_group": "fast_food", "why": "w"},
    {"concept": "Grain bowls", "tag_query": "bowls", "rent_group": "fast_food", "why": "w"},
]}
BASELINE = {"candidates": [{"concept": "Ramen shop", "why": "a"}, {"concept": "Coffee shop", "why": "b"},
                           {"concept": "Wine bar", "why": "c"}, {"concept": "Pizza", "why": "d"}],
            "competitors": ["Leopardi", "Totally Invented Bistro", 7]}
RENT = [{"group": "restaurant", "rent_max": 3000.0, "ci90": [2500.0, 3600.0], "viable": True},
        {"group": "fast_food", "rent_max": 2000.0, "ci90": [1500.0, 2400.0], "viable": True}]


def chat_script(memo="## Recommendation\nRamen counter.\n\n" + "x" * 250):
    def chat(messages):
        text = messages[-1]["content"]
        if "Without any external data" in text:
            return {"content": json.dumps(BASELINE), "provider": "fake"}
        if "Propose" in text:
            return {"content": "Sure:\n" + json.dumps(CANDIDATES), "provider": "fake"}
        return {"content": memo, "provider": "fake"}
    return chat


def no_llm(messages):
    raise RuntimeError("no provider")


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeQloo()
        patches = [mock.patch.object(qloo, "exec_cached", side_effect=self.fake),
                   mock.patch("tenant.geo.reverse", return_value={"areas": ["Sample Park"], "city": "Chicago", "postcode": "60622"}),
                   mock.patch("tenant.geo.geocode", side_effect=AssertionError("no geocoding expected")),
                   mock.patch("tenant.scan.unit", return_value=(200, {"calibrated": True, "rent_ceiling": RENT,
                                                                      "currency": "USD", "note": "model", "cell": 7})),
                   mock.patch("tenant.tgi.load", return_value=self.venues())]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def venues(self):
        return None

    def run_all(self, **kw):
        kw.setdefault("chat_fn", chat_script())
        return list(tenant.run("chicago", lat=41.9, lon=-87.68, today=datetime.date(2026, 10, 13), **kw))

    def kinds(self, events, kind):
        return [d for k, d in events if k == kind]

    def test_full_run_uses_eight_workflows_within_budget(self):
        ev = self.run_all(asking_rent=2800)
        budget = self.kinds(ev, "budget")[0]
        self.assertLessEqual(budget["qloo_calls"], tenant.BUDGET)
        self.assertEqual(set(budget["workflows"]), {"recommend", "entity_tags", "audience_demographics", "find_tags",
                                                    "rank", "compare_audiences", "trends", "where_popular"})
        self.assertEqual([k for k, _ in ev][-4:], ["shortlist", "baseline", "memo", "budget"])

    def test_alcohol_led_candidates_and_benchmarks_are_excluded(self):
        ev = self.run_all()
        concepts = [c["concept"] for c in self.kinds(ev, "candidates")[0]["concepts"]]
        self.assertNotIn("Craft beer taproom", concepts)
        self.assertIn("Salad bar", concepts)
        for s in self.kinds(ev, "shortlist")[0]["concepts"]:
            self.assertNotEqual(s["benchmark"]["name"], "Sample Wine Bar")
        self.assertNotIn("Beer", self.kinds(ev, "profile")[0]["tags"])
        self.assertNotIn("Visa", self.kinds(ev, "profile")[0]["tags"])

    def test_scores_white_space_and_rent_join(self):
        ev = self.run_all(asking_rent=2800)
        rows = {s["concept"]: s for s in self.kinds(ev, "shortlist")[0]["concepts"]}
        ramen = rows["Ramen counter"]
        self.assertEqual((ramen["demand"], ramen["position"], ramen["local_supply"], ramen["white_space"]), (100, 1, 0, 100))
        self.assertEqual(ramen["overlap"], 70)  # mean of Umami 0.8 and Casual 0.6; generic "Place" dropped
        self.assertEqual(ramen["score"], 94)
        self.assertEqual(ramen["call"], "GO")  # 2800 is within the restaurant ceiling 3000
        self.assertEqual(rows["Salad bar"]["call"], "NO-GO")  # 2800 > fast-food 90% range top 2400
        self.assertIn("cafés", rows["Specialty coffee"]["rent_note"])
        self.assertIsNone(rows["Grain bowls"].get("call"))  # ranked but beyond the deep checks

    def test_ambiguous_trend_brand_retries_with_first_candidate(self):
        ev = self.run_all()
        trend_calls = [p for op, p in self.fake.calls if op == "trends"]
        self.assertEqual([c["entities"] for c in trend_calls], [["Ambiguous Chain"], ["BR1"]])
        self.assertEqual(trend_calls[0]["start_date"], "2026-04-14")
        ramen = next(s for s in self.kinds(ev, "shortlist")[0]["concepts"] if s["concept"] == "Ramen counter")
        self.assertEqual(ramen["trend"], {"brand": "Chain One", "percentile": 62, "change": 2.0, "weeks": 2,
                                          "from": "2026-05-01", "to": "2026-10-01"})
        self.assertEqual(ramen["hotspot_km"], 1.11)

    def test_baseline_claims_are_marked_against_qloo(self):
        base = self.kinds(self.run_all(), "baseline")[0]
        status = {r["concept"]: r["status"] for r in base["candidates"]}
        self.assertEqual(status, {"Ramen shop": "confirmed", "Coffee shop": "contradicted", "Wine bar": "excluded",
                                  "Pizza": "not tested"})

    def test_without_llm_catalog_and_template_finish_the_run(self):
        ev = self.run_all(chat_fn=no_llm)
        self.assertEqual(self.kinds(ev, "candidates")[0]["source"], "catalog")
        memo = self.kinds(ev, "memo")[0]
        self.assertEqual(memo["author"], "template")
        self.assertIn("## Limits", memo["markdown"])
        self.assertEqual(self.kinds(ev, "baseline")[0], {"unavailable": True})

    def test_past_the_deadline_the_run_skips_the_llm_and_still_finishes(self):
        ticks = iter([0.0])
        clock = lambda: next(ticks, tenant.DEADLINE)  # noqa: E731  starts at 0, then is out of time
        with mock.patch("tenant.llm.chat", side_effect=AssertionError("no LLM call expected")):
            ev = self.run_all(chat_fn=None, clock=clock)
        self.assertEqual(self.kinds(ev, "candidates")[0]["source"], "catalog")
        self.assertEqual(self.kinds(ev, "memo")[0]["author"], "template")
        self.assertEqual(ev[-1][0], "budget")
        self.assertTrue(self.kinds(ev, "shortlist")[0]["concepts"])

    def test_llm_memo_always_gets_limits(self):
        memo = self.kinds(self.run_all(), "memo")[0]
        self.assertEqual(memo["author"], "fake")
        self.assertIn("## Limits", memo["markdown"])

    def test_steps_never_contain_the_key(self):
        with mock.patch.dict("os.environ", {"QLOO_API_KEY": "secret-test-key"}):
            ev = self.run_all()
        self.assertNotIn("secret-test-key", json.dumps(ev))

    def test_failed_calls_degrade_without_crashing(self):
        self.fake.fail = {"compare_audiences", "where_popular", "trends"}
        ev = self.run_all()
        self.assertTrue(self.kinds(ev, "memo"))
        self.assertTrue(any(s.get("error") for s in self.kinds(ev, "step")))

    def test_no_area_places_is_an_error(self):
        with mock.patch.object(qloo, "exec_cached", return_value=(ok(results=[]), False)):
            ev = self.run_all()
        self.assertEqual(ev[-1][0], "error")

    def test_unnamed_point_is_an_error(self):
        with mock.patch("tenant.geo.reverse", return_value=None):
            ev = self.run_all()
        self.assertEqual(ev, [("error", "Could not name that point; type the neighborhood instead.")])


class TasteGapPipelineTest(unittest.TestCase):
    """The same run with an OSM venue file: one extra city rank call, no Qloo local-supply calls."""
    setUp, run_all, kinds = PipelineTest.setUp, PipelineTest.run_all, PipelineTest.kinds

    def venues(self):
        from tests import test_tgi
        return test_tgi.city(near_ramen=0, near_coffee=12)

    def test_osm_supply_replaces_qloo_local_calls(self):
        ev = self.run_all(asking_rent=2800)
        ranks = [p["signal_location"] for op, p in self.fake.calls if op == "rank"]
        self.assertEqual(sorted(ranks), ["Chicago", "Sample Park, Chicago"])
        local = [p for op, p in self.fake.calls if op == "recommend" and p.get("include_tags")
                 and p.get("filter_location") == p.get("signal_location")]
        # only concepts with no OSM venue anywhere in the city (salad, pho here) still ask Qloo
        self.assertEqual(sorted(p["include_tags"][0] for p in local), ["urn:tag:t:pho", "urn:tag:t:salad"])
        osm = [s for s in self.kinds(ev, "step") if s.get("source") == "OpenStreetMap"]
        self.assertTrue(osm)
        self.assertNotIn("input", osm[0])
        budget = self.kinds(ev, "budget")[0]
        self.assertLessEqual(budget["qloo_calls"], tenant.BUDGET)
        self.assertIn("trends", budget["workflows"])
        self.assertIn("where_popular", budget["workflows"])

    def test_taste_gap_fields(self):
        ev = self.run_all()
        rows = {s["concept"]: s for s in self.kinds(ev, "shortlist")[0]["concepts"]}
        ramen, coffee = rows["Ramen counter"], rows["Specialty coffee"]
        self.assertGreater(ramen["overindex"], 0)  # 1st locally, 3rd citywide
        self.assertLess(coffee["overindex"], 0)  # 3rd locally, 1st citywide
        self.assertEqual(ramen["supply_source"], "OpenStreetMap")
        self.assertEqual(ramen["white_space"], round(100 * ramen["p_under"]))
        self.assertGreater(ramen["p_under"], coffee["p_under"])
        self.assertGreater(ramen["tgi"], coffee["tgi"])
        self.assertIn(ramen["tgi_label"], {"white space", "neutral"})
        self.assertIn("uncalibrated", self.kinds(ev, "shortlist")[0]["method"])
        self.assertIn("else the Qloo count", self.kinds(ev, "shortlist")[0]["method"])

    def test_memos_attribute_osm_supply(self):
        self.assertIn("must be attributed to OpenStreetMap", tenant.MEMO_PROMPT)
        memo = self.kinds(self.run_all(chat_fn=no_llm), "memo")[0]["markdown"]
        self.assertIn("OpenStreetMap-mapped venues within 600 m", memo)
        self.assertIn("local places serve it (Qloo)", memo)  # pho has no OSM venue here: Qloo count, labelled

    def test_claim_ledger(self):
        base = self.kinds(self.run_all(), "baseline")[0]
        status = {r["concept"]: r["status"] for r in base["candidates"]}
        self.assertIn(status["Ramen shop"], {"confirmed", "partly"})
        self.assertTrue(base["candidates"][0]["evidence"].startswith("Taste Gap:"))
        self.assertEqual(status["Wine bar"], "excluded")
        self.assertEqual([(v["name"], v["status"]) for v in base["venues"]],
                         [("Leopardi", "exists"), ("Totally Invented Bistro", "not found")])
        L = base["ledger"]
        self.assertEqual(L["llm_venues"], {"exists": 1, "not found": 1, "ambiguous": 0, "named": 2, "radius_m": 500})
        self.assertEqual(L["qloo_venues"]["named"], 3)  # the area favourites, checked the same way
        self.assertEqual(L["source"], "OpenStreetMap")
        self.assertEqual(sum(L["concepts"].values()), 4)

    def test_ledger_verdict_rules(self):
        rows = [{"concept": c, "tag": None, "tgi_label": g, "overindex": d, "local_supply": 1, "supply_expected": 1.0}
                for c, g, d in [("ramen", "white space", 1.0), ("pho", "neutral", 0.2), ("tacos", "proven demand, crowded", 1.0),
                                ("salad", "neutral", -0.3), ("burger", "avoid", -1.0)]]
        base = {"candidates": [{"concept": c, "why": "", "excluded": False} for c in ("Ramen", "Pho", "Tacos", "Salad", "Burger", "Sushi")]}
        out = tenant.judge_baseline(base, rows)
        self.assertEqual([r["status"] for r in out["candidates"]],
                         ["confirmed", "confirmed", "contradicted", "partly", "contradicted", "not tested"])
        self.assertIsNone(out["ledger"]["llm_venues"])
        self.assertIsNone(out["ledger"]["source"])

    def test_unmapped_concept_falls_back_to_qloo_supply(self):
        with mock.patch("tenant.tgi.cuisines_for", side_effect=lambda c, t=None: set() if "Pho" in c else {"ramen"}):
            ev = self.run_all()
        rows = {s["concept"]: s for s in self.kinds(ev, "shortlist")[0]["concepts"]}
        self.assertEqual(rows["Pho house"]["supply_source"], "Qloo")
        self.assertEqual(rows["Ramen counter"]["supply_source"], "OpenStreetMap")


class DeadlineTest(unittest.TestCase):
    def test_timeout_shrinks_to_the_time_left(self):
        now = [0.0]
        chat = mock.Mock(return_value={"content": "ok"})
        call = tenant.deadline_chat(0.0, clock=lambda: now[0], chat=chat)
        with mock.patch("tenant.llm.provider_names", return_value=["community"]):
            call([])
            self.assertEqual(chat.call_args.kwargs["timeout"], tenant.LLM_TIMEOUT)
            now[0] = tenant.DEADLINE - 20
            call([])
            self.assertEqual(chat.call_args.kwargs["timeout"], 20)
        with mock.patch("tenant.llm.provider_names", return_value=["gemini", "community"]):
            call([])
            self.assertEqual(chat.call_args.kwargs["timeout"], 10)
            now[0] = tenant.DEADLINE - tenant.MIN_LLM_WINDOW + 1
            with self.assertRaises(TimeoutError):
                call([])
        self.assertEqual(chat.call_count, 3)


class LedgerTest(unittest.TestCase):
    def test_cache_is_scoped_to_one_run(self):
        a, b = tenant.Ledger(), tenant.Ledger()
        with mock.patch.object(qloo, "exec_workflow", return_value=ok()) as ex:
            self.assertFalse(a.call("find_tags", {"query": "x"}, "p")[1]["cached"])
            self.assertTrue(a.call("find_tags", {"query": "x"}, "p")[1]["cached"])
            self.assertFalse(b.call("find_tags", {"query": "x"}, "p")[1]["cached"])
        self.assertEqual(ex.call_count, 2)

    def test_budget_and_cache_hits(self):
        led = tenant.Ledger(budget=2)
        with mock.patch.object(qloo, "exec_cached", side_effect=[(ok(), True), (ok(), False), (ok(), False)]):
            steps = [led.call("find_tags", {"query": str(i)}, "p")[1] for i in range(4)]
        self.assertEqual([s.get("cached") for s in steps[:3]], [True, False, False])
        self.assertEqual(steps[3]["skipped"], "call budget reached")
        self.assertEqual((led.network, led.cached), (2, 1))

    def test_rate_limited_call_is_retried_once(self):
        led = tenant.Ledger(sleep=lambda s: None)
        limited = {"status": "error", "error": {"code": "QLOO_RATE_LIMIT"}}
        with mock.patch.object(qloo, "exec_cached", side_effect=[(limited, False), (ok(), False)]):
            res, step = led.call("rank", {}, "p")
        self.assertEqual((res["status"], step.get("error"), led.network), ("ok", None, 2))

    def test_exception_becomes_step_error(self):
        led = tenant.Ledger()
        with mock.patch.object(qloo, "exec_cached", side_effect=RuntimeError("boom")):
            res, step = led.call("rank", {}, "p")
        self.assertIsNone(res)
        self.assertEqual(step["error"], "RuntimeError")


class HelpersTest(unittest.TestCase):
    def test_alcohol_led(self):
        for s in ("wine bar", "Craft brewery", "cocktail lounge", "Irish pub", "izakaya", "Sports Bar & Grill",
                  "Federales Tacos & Tequila", "Mezcaleria Las Flores", "Gin & Tonic Club"):
            self.assertTrue(tenant.alcohol_led(s), s)
        for s in ("salad bar", "juice bar", "espresso bar", "ramen counter", "bakery", "sushi bar", "Barbecue",
                  "Ginger Thai", "Rumi Persian Grill", "Pizza Margherita"):
            self.assertFalse(tenant.alcohol_led(s), s)

    def test_usable_benchmark_keeps_licensed_restaurants(self):
        bar = {"name": "Le Comptoir", "description": "Lively wine bar with small plates"}
        resto = {"name": "Bistro Sample", "description": "French bistro with a wine list"}
        self.assertIs(tenant.usable_benchmark([bar, resto]), resto)

    def test_pick_tag_prefers_place_scoped(self):
        tag, alts = tenant.pick_tag(ok(results=[
            {"id": "urn:tag:keyword:media:ramen", "name": "Ramen", "type": "urn:tag:keyword:media"},
            {"id": "urn:tag:cuisine:qloo:ramen", "name": "Ramen", "type": "urn:tag:cuisine:qloo"},
            {"id": "urn:tag:category:place:ramen_restaurant", "name": "Ramen Restaurant", "type": "urn:tag:category:place"}]))
        self.assertEqual(tag["id"], "urn:tag:category:place:ramen_restaurant")
        self.assertEqual(len(alts), 2)
        self.assertEqual(tenant.pick_tag(ok(results=[{"id": "urn:tag:keyword:media:x", "name": "X",
                                                      "type": "urn:tag:keyword:media"}]))[0], None)

    def test_demand_scores_are_positional_within_one_call(self):
        res = ok(results=[{"entity_id": "b", "affinity": 0.9991}, {"entity_id": "a", "affinity": 0.9995},
                          {"entity_id": "c", "affinity": 0.9990}])
        self.assertEqual(tenant.demand_scores(res, {"a": ["A"], "b": ["B", "B2"], "c": ["C"]}),
                         {"A": (100, 1, 3), "B": (50, 2, 3), "B2": (50, 2, 3), "C": (0, 3, 3)})

    def test_tenant_score_renormalizes_missing_parts(self):
        self.assertEqual(tenant.tenant_score(100, 100, 50), 90)
        self.assertEqual(tenant.tenant_score(100, 0, None), 56)
        self.assertIsNone(tenant.tenant_score(None, None, None))

    def test_rent_check_and_verdict(self):
        rows = [dict(RENT[0]), {"group": "fast_food", "viable": False, "rent_max": None, "note": "Not viable"}]
        self.assertEqual(tenant.rent_check("restaurant", rows, 3200)[2], "ADJUST")
        self.assertEqual(tenant.rent_check("restaurant", rows, 3700)[2], "NO-GO")
        self.assertIsNone(tenant.rent_check("restaurant", rows, None)[2])
        self.assertEqual(tenant.rent_check("fast_food", rows, None)[2], "NO-GO")
        self.assertIn("not calibrated", tenant.rent_check("restaurant", None, 100)[1])
        self.assertEqual(tenant.verdict(80, "ADJUST"), "ADJUST")
        self.assertEqual(tenant.verdict(30, "ADJUST"), "NO-GO")
        self.assertEqual(tenant.verdict(65, None), "GO")

    def test_trend_summary_and_demographics(self):
        self.assertIsNone(tenant.trend_summary(ok(series=[])))
        self.assertEqual(tenant.demographics(ok(results=[{"query": {"age": {"25_to_29": 0.123}}}])),
                         {"age": {"25_to_29": 0.12}})
        self.assertEqual(tenant.demographics(ok(results=[])), {})

    def test_template_memo_has_limits_and_no_doubled_city(self):
        memo = tenant.template_memo("Sample Park, Chicago", "Chicago", [], {})
        self.assertTrue(memo.startswith("# Tenant memo: Sample Park, Chicago\n"))
        self.assertIn("## Limits", memo)
        self.assertIn(tenant.BACKTEST_NOTE, memo)

    def test_ensure_limits_always_reports_the_backtest(self):
        self.assertIn(tenant.BACKTEST_NOTE, tenant.ensure_limits("# Memo\n## Recommendation\nGo."))
        llm_limits = "# Memo\n## Limits\n- Qloo is aggregate taste."
        self.assertTrue(tenant.ensure_limits(llm_limits).endswith(tenant.BACKTEST_NOTE))
        already = llm_limits + "\n- A Chicago backtest found no gain over a citywide baseline."
        self.assertEqual(tenant.ensure_limits(already), already)
        elsewhere = "# Memo\n## Recommendation\nOur backtest says go.\n## Limits\n- Aggregate taste."
        self.assertTrue(tenant.ensure_limits(elsewhere).endswith(tenant.BACKTEST_NOTE))

    def test_propose_tops_up_from_catalog(self):
        cands, source = tenant.propose("A", "C", [], [], {}, lambda m: {"content": "no json", "provider": "p"})
        self.assertEqual((len(cands), source), (tenant.N_CANDIDATES + 2, "p"))
        self.assertFalse(any(tenant.alcohol_led(c["concept"]) for c in cands))


if __name__ == "__main__":
    unittest.main()
