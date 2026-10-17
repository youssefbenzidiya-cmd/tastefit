"""The speed audit does not raise the call budget or the run deadline."""
import unittest
from pathlib import Path

import tenant

DOC = Path(__file__).resolve().parent.parent / "docs" / "SPEED-AUDIT.md"


class SpeedAudit(unittest.TestCase):
    def test_budget_deadline_and_parallelism_stay_put(self):
        self.assertEqual(tenant.BUDGET, 25)
        self.assertEqual(tenant.DEADLINE, 230)
        self.assertEqual(tenant.PARALLEL, 3)
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("The call budget stays 25.", text)
        self.assertIn("The run deadline stays 230s.", text)
        self.assertIn("No safe change beats these gaps.", text)
        self.assertNotIn("urn:", text)


if __name__ == "__main__":
    unittest.main()
