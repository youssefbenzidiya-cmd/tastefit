"""The open challenge script accepts a stable certificate and names a missed move."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import portfolio

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "docs" / "verify_certificate.py"
PUBLISHED = ROOT / "docs" / "published-street.json"

UNITS = [
    {"id": "Example A", "rent": 2600},
    {"id": "Example B", "rent": 2400},
    {"id": "Example C", "rent": 2200},
    {"id": "Example D", "rent": 2000},
]
CONCEPTS = [
    {"id": "Ramen shop", "category": "restaurant", "fit": 78, "overlap": 55,
     "strong": True, "avoid": False, "rent_first": False,
     "rent_row": {"viable": True, "rent_max": 3230, "ci90": [2800, 4000]}},
    {"id": "Chicken sandwich", "category": "fast_food", "fit": 58, "overlap": 40,
     "strong": False, "avoid": False, "rent_first": False,
     "rent_row": {"viable": True, "rent_max": 2790, "ci90": [1930, 4020]}},
    {"id": "Grain bowl", "category": "restaurant", "fit": 36, "overlap": 22,
     "strong": False, "avoid": False, "rent_first": False,
     "rent_row": {"viable": True, "rent_max": 3230, "ci90": [2800, 4000]}},
]


def street(matches):
    return {"units": UNITS, "concepts": CONCEPTS, "matches": matches,
            "notice": "Stated scores and rents. No Qloo response. Not a vetted address."}


def run(doc):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "street.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True, cwd=ROOT)


class VerifyCertificate(unittest.TestCase):
    def test_published_street_has_no_improving_move(self):
        doc = json.loads(PUBLISHED.read_text())
        self.assertIn("No Qloo response", doc["notice"])
        self.assertNotIn("urn:", PUBLISHED.read_text())
        result = subprocess.run([sys.executable, str(SCRIPT), str(PUBLISHED)], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No improving move:", result.stdout)
        fresh = portfolio.assign(doc["units"], doc["concepts"], k=1)
        self.assertEqual([(m["unit"], m["concept"]) for m in doc["matches"]],
                         [(m["unit"], m["concept"]) for m in fresh["matches"]])

    def test_a_swapped_assignment_is_an_improving_move(self):
        stable = portfolio.assign(UNITS, CONCEPTS, k=1)["matches"]
        broken = [dict(match) for match in stable]
        broken[0]["concept"], broken[1]["concept"] = broken[1]["concept"], broken[0]["concept"]
        result = run(street(broken))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Improving move:", result.stdout)

    def test_bad_input_exits_2(self):
        result = run({"units": UNITS})
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a street certificate", result.stderr)


if __name__ == "__main__":
    unittest.main()
