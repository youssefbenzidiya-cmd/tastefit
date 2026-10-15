"""The Wicker Park button loads an aggregates-only snapshot. No Qloo response is stored."""
import json
import unittest
from pathlib import Path

import app
import ledger
import portfolio
import tenant
import trial

FILE = Path(__file__).resolve().parent.parent / "demo" / "wicker-park.json"
CHICAGO = "f08f78d5b2a1c2c1b46c87429e30e1e1bf9c912450b19a2ade608891a31d3b46"
RAW_KEYS = {"urn", "entity_id", "entities", "tag_urns", "popularity", "raw", "response", "request", "query_id"}


def _keys(value, found):
    if isinstance(value, dict):
        found.update(value)
        for item in value.values():
            _keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _keys(item, found)


class WickerExample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(FILE.read_text())

    def test_labelled_saved_example_with_no_qloo_payload(self):
        self.assertEqual(self.doc["label"], "saved example from 2026-10-15")
        self.assertIn("saved example from 2026-10-15", self.doc["_notice"])
        self.assertIn("No Qloo response", self.doc["_notice"])
        self.assertIn("Not a vetted address", self.doc["notice"])
        text = FILE.read_text()
        self.assertNotIn("urn:", text)
        found = set()
        _keys(self.doc, found)
        self.assertFalse(found & RAW_KEYS)

    def test_shortlist_certificate_split_whatif_and_trial_are_present(self):
        concepts = self.doc["shortlist"]["concepts"]
        self.assertEqual([c["call"] for c in concepts], ["GO", "ADJUST", "NO-GO"])
        self.assertIn("stated aggregates", self.doc["shortlist"]["method"])
        self.assertNotIn("weights", self.doc["shortlist"])
        self.assertGreaterEqual(len(self.doc["portfolio"]["matches"]), 3)
        self.assertIn("No improving move:", self.doc["portfolio"]["proof"])
        self.assertTrue(self.doc["portfolio"]["rent_split"]["rows"])
        self.assertEqual(self.doc["portfolio"]["rent_split"]["label"], "suggested, uncalibrated")
        self.assertIn("No taste data in this run", self.doc["what_if"]["added_note"])
        self.assertEqual(self.doc["what_if"]["qloo_calls"], 0)
        trial = self.doc["shortlist"]["trial"]
        self.assertTrue(trial["sentence"].startswith("Test before you lease"))
        self.assertIn("no interest and no discounting", trial["sentence"])
        self.assertIn("not a vetted address", trial["sentence"])
        self.assertIn("did not beat a citywide baseline", self.doc["shortlist"]["footfall"]["sentence"])
        memo = self.doc["memo"]["markdown"]
        self.assertIn("## Limits", memo)
        self.assertIn("Street certificate", memo)
        self.assertIn("not a vetted address", memo)

    def test_certificate_and_whatif_match_the_street_code(self):
        stored = self.doc["portfolio"]
        fresh = portfolio.assign(self.doc["units"], self.doc["concepts"], k=1)
        self.assertEqual(stored["proof"], fresh["proof"])
        self.assertEqual(
            [(m["unit"], m["concept"], m["verdict"], m["rent"]) for m in stored["matches"]],
            [(m["unit"], m["concept"], m["verdict"], m["rent"]) for m in fresh["matches"]])
        self.assertEqual(stored["rent_split"]["total_asking"], fresh["rent_split"]["total_asking"])
        self.assertEqual(stored["rent_split"]["total_suggested"], fresh["rent_split"]["total_suggested"])
        again = portfolio.what_if(self.doc["units"], stored["inputs"], add="corner bakery")
        self.assertEqual(self.doc["what_if"]["changes"], again["changes"])
        self.assertEqual(self.doc["what_if"]["after"]["proof"], again["after"]["proof"])

    def test_trial_sentence_matches_trial_recommend(self):
        top = self.doc["shortlist"]["concepts"][0]
        row = next(c["rent_row"] for c in self.doc["concepts"] if c["id"] == top["concept"])
        plan = trial.recommend("Wicker Park, Chicago", top, "chicago", row, self.doc["asking_rent"],
                               self.doc["shortlist"]["voi"], self.doc["shortlist"]["trial"]["ts"])
        self.assertEqual(plan["sentence"], self.doc["shortlist"]["trial"]["sentence"])
        self.assertEqual(plan["sha256"], self.doc["shortlist"]["trial"]["sha256"])

    def test_footfall_sentence_matches_the_committed_table(self):
        signal = tenant.footfall_signal("Wicker Park", "Chicago")
        self.assertEqual(signal["sentence"], self.doc["shortlist"]["footfall"]["sentence"])

    def test_page_exposes_the_button_and_the_snapshot(self):
        page = app.render_index("n").decode()
        self.assertIn("Try the Wicker Park example", page)
        self.assertIn('data-action="wicker-example"', page)
        self.assertIn("Who should rent this unit?", page)
        script = (app.ROOT / "static" / "app.js").read_text()
        self.assertIn("/wicker-example.json", script)
        self.assertIn("Example rank", script)
        self.assertIn("if (!W) return ''", script)
        self.assertIn("saved example from 2026-10-15", script)
        code, ctype, body, _csp = app.Handler._page(None, "/wicker-example.json")
        self.assertEqual(code, 200)
        self.assertEqual(ctype, "application/json")
        self.assertEqual(json.loads(body)["label"], "saved example from 2026-10-15")

    def test_chicago_prediction_fingerprint_is_unchanged(self):
        batch = ledger.load()["batches"][0]
        self.assertEqual(batch["sha256"], CHICAGO)
        self.assertEqual(batch["area"], "Wicker Park, Chicago")


if __name__ == "__main__":
    unittest.main()
