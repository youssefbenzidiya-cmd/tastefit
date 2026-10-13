"""The /demo fixture is synthetic and internally consistent with the real site maths and scorecard code."""
import json
import unittest
from pathlib import Path

import agent
import geo

FIXTURE = Path(__file__).resolve().parent.parent / "demo" / "sample-report.json"


class SampleReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(FIXTURE.read_text())
        cls.events = {e["event"]: e["data"] for e in cls.doc["events"]}

    def test_labelled_synthetic(self):
        self.assertIn("SYNTHETIC", self.doc["_notice"])
        self.assertIn("not live Qloo output", self.events["demo"]["notice"])
        self.assertIn("synthetic data", self.events["memo"])

    def test_sample_concept_is_not_alcohol_led(self):
        import tenant
        self.assertFalse(tenant.alcohol_led(self.doc["concept"]))
        self.assertFalse(tenant.alcohol_led(self.events["demo"]["concept"]))
        self.assertNotRegex(FIXTURE.read_text().lower(), r"wine|liquor|cocktail|decanter")

    def test_only_known_events_with_small_delays(self):
        kinds = [e["event"] for e in self.doc["events"]]
        self.assertEqual(kinds[0], "demo")
        self.assertEqual(kinds[-3:], ["geo", "scorecard", "memo"])
        self.assertTrue(set(kinds) <= {"demo", "step", "geo", "scorecard", "memo"})
        self.assertTrue(all(0 <= e["delay_ms"] <= 2000 for e in self.doc["events"]))
        self.assertTrue(all(urn.startswith("urn:tag:sample:") for e in self.doc["events"] if e["event"] == "step"
                            for urn in e["data"]["args"].get("tag_urns", [])))

    def test_site_numbers_come_from_geo_analyze(self):
        g = self.events["geo"]
        recomputed = geo.analyze(g["demand"], g["competition"])
        self.assertEqual({k: v for k, v in g.items() if k not in ("demand", "competition")}, recomputed)

    def test_scorecard_comes_from_build_scorecard(self):
        g = self.events["geo"]
        self.assertEqual(self.events["scorecard"], agent.build_scorecard(self.doc["model_verdict"], geo.analyze(g["demand"], g["competition"])))

    def test_memo_matches_scorecard_and_keeps_limits(self):
        sc, memo = self.events["scorecard"], self.events["memo"]
        self.assertIn(f"## Verdict: {sc['call']} (fit score {sc['fit']})", memo)
        self.assertIn("## Limits", memo)
        self.assertIn("never a vetted address", memo)
        self.assertNotIn("```", memo)


if __name__ == "__main__":
    unittest.main()
