"""Prediction fingerprints. The committed batch is public fields only; fixtures here are synthetic."""
import unittest

import ledger


class Fingerprint(unittest.TestCase):
    def entry(self, verdict="ADJUST"):
        return {"concept": "Ramen counter", "area": "Wicker Park, Chicago", "verdict": verdict, "ts": "2026-10-14T14:00:00Z",
                "shortlist": [{"concept": "Ramen counter", "verdict": verdict}, {"concept": "Salad bar", "verdict": "NO-GO"}]}

    def test_hash_covers_the_five_fields_and_ignores_the_rest(self):
        base = self.entry()
        extra = dict(base, sha256="nope", note="qloo payload", tag={"id": "urn:secret"})
        self.assertEqual(ledger.fingerprint(base), ledger.fingerprint(extra))
        self.assertEqual(len(ledger.fingerprint(base)), 64)
        self.assertNotIn("qloo", ledger.canonical(extra).lower())
        self.assertNotIn("urn", ledger.canonical(extra))

    def test_a_different_verdict_is_a_different_fingerprint(self):
        self.assertNotEqual(ledger.fingerprint(self.entry("GO")), ledger.fingerprint(self.entry("NO-GO")))

    def test_shortlist_keeps_concept_and_verdict_only(self):
        raw = [{"concept": "Ramen counter", "verdict": "GO", "score": 90, "tag": {"name": "secret place"}}]
        entry = ledger.from_shortlist("Wicker Park, Chicago", raw, "2026-10-14T14:00:00Z")
        self.assertEqual(entry["shortlist"], [{"concept": "Ramen counter", "verdict": "GO"}])
        self.assertEqual(entry["sha256"], ledger.fingerprint(entry))
        self.assertNotIn("secret", ledger.canonical(entry))

    def test_page_escapes_and_states_the_brier_rule(self):
        doc = {"protocol": {"brier": "Brier (Brier 1950).", "outcome": "o in {0,1}.", "comparator": "skill may be negative."},
               "batches": [{"area": "Wicker Park, Chicago", "concept": "<script>", "verdict": "ADJUST",
                            "ts": "2026-10-14T14:00:00Z", "sha256": "abc", "brier_sha256": "def",
                            "check_on": "2026-11-17", "check_against": "Chicago business licences",
                            "lat": 41.9, "lon": -87.7, "note": "not a calibrated probability",
                            "forecasts": [{"concept": "Ramen & co", "verdict": "ADJUST", "p": 0.5}]}]}
        html = ledger.page(doc)
        self.assertIn("pre-registered Chicago batch, not the Print memo", html)
        self.assertIn("youssefbenzidiya@gmail.com", html)
        self.assertIn("Brier 1950", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Ramen &amp; co", html)
        self.assertIn("2026-11-17", html)
        self.assertIn("First listed concept", html)
        self.assertIn("not a ranking", html)
        self.assertIsNone(ledger.probabilities({"shortlist": [{"concept": "x", "verdict": "unscored"}]}))
        mapped = ledger.probabilities({"shortlist": [{"concept": "x", "verdict": "GO"}, {"concept": "y", "verdict": "NO-GO"}]})
        self.assertEqual([row["p"] for row in mapped], [0.66, 0.33])


class CommittedBatch(unittest.TestCase):
    def test_the_chicago_batch_matches_its_hash_and_does_not_invent_a_score(self):
        doc = ledger.load()
        self.assertEqual(len(doc["batches"]), 1)
        batch = doc["batches"][0]
        self.assertIn("Chicago", batch["area"])
        self.assertGreaterEqual(batch["check_on"], "2026-11-17")
        self.assertEqual(batch["verdict"], "unscored")
        self.assertEqual(batch["sha256"], ledger.fingerprint(batch))
        self.assertNotIn("brier_sha256", batch)
        self.assertIn("rate_limit", batch["note"].lower())
        self.assertTrue(all("p" not in row for row in batch["shortlist"]))
        canonical = ledger.canonical(batch)
        self.assertNotIn("qloo", canonical.lower())
        self.assertNotIn("urn:", canonical)
        html = ledger.page(doc)
        self.assertIn(batch["sha256"], html)
        self.assertIn("Brier score is not defined", html)
        self.assertIn("not a ranking", html)
        self.assertIsNone(ledger.probabilities(batch))
        self.assertNotIn("p=", html)


class StreetCertificate(unittest.TestCase):
    def outcome(self, concept="ramen"):
        return {"matches": [{"unit": "Unit 1", "rent": 2800, "concept": concept, "fit": 80, "overlap": 70},
                            {"unit": "Unit 2", "rent": 4200.0, "concept": "salad", "fit": 50},
                            {"unit": "Unit 3", "rent": 5100, "concept": "coffee", "fit": 60}],
                "replacements": 4, "swaps": 3, "needs_rent_flexibility": False,
                "qloo": "urn:secret", "proof": "No improving move: 4 replacements and 3 swaps checked."}

    def test_the_street_hash_ignores_qloo_fields_and_changes_with_the_assignment(self):
        text = ledger.street_canonical(self.outcome())
        self.assertNotIn("qloo", text.lower())
        self.assertNotIn("urn", text)
        self.assertNotIn("overlap", text)
        self.assertNotIn("fit", text)
        self.assertEqual(ledger.street_fingerprint(self.outcome()), ledger.street_fingerprint(self.outcome()))
        self.assertNotEqual(ledger.street_fingerprint(self.outcome()),
                            ledger.street_fingerprint(self.outcome("pho")))
        entry = {"concept": "ramen", "area": "Sample", "verdict": "GO", "ts": "2026-10-14T14:00:00Z",
                 "shortlist": [{"concept": "ramen", "verdict": "GO"}]}
        self.assertNotIn("needs_rent_flexibility", ledger.canonical(entry))


if __name__ == "__main__":
    unittest.main()
