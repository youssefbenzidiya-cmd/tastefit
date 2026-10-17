"""The live step says how long the current step has been quiet, and the clock stays.

The threshold is checked in Python so the unit-test image does not need Node.
The same function body must be the one in static/app.js.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "static" / "app.js"

STALL_FN = """\
const STALL_AFTER_MS = 8000;
function stallOut(quietMs) {
  if (quietMs <= STALL_AFTER_MS) return 'Working';
  return `Still working… ${Math.floor(quietMs / 1000)}s on this step`;
}
"""


def stall_out(quiet_ms):
    """Mirror of stallOut. Quiet time at or under 8s stays 'Working'."""
    if quiet_ms <= 8000:
        return "Working"
    return f"Still working… {quiet_ms // 1000}s on this step"


class ProgressHonesty(unittest.TestCase):
    def test_stall_copy_and_clock_stay_in_the_live_step(self):
        src = APP_JS.read_text(encoding="utf-8")
        self.assertIn(STALL_FN, src)
        self.assertIn('id="live-title">${esc(nextTitle())}', src)
        self.assertIn('id="live-out">${esc(stallOut(quietMs()))}', src)
        self.assertIn('id="clock"', src)
        self.assertIn("clockTimer = setInterval(tickRun, 1000)", src)
        self.assertIn("state.lastEventAt = Date.now()", src)
        self.assertIn("if (HANDLERS[event]) HANDLERS[event](data)", src)
        self.assertIn("data ? JSON.parse(data) : null", src)
        self.assertIn("Who should rent this unit?", (ROOT / "index.html").read_text(encoding="utf-8"))

    def test_stall_line_waits_until_after_eight_seconds(self):
        self.assertEqual(stall_out(0), "Working")
        self.assertEqual(stall_out(7999), "Working")
        self.assertEqual(stall_out(8000), "Working")
        self.assertEqual(stall_out(8001), "Still working… 8s on this step")
        self.assertEqual(stall_out(9000), "Still working… 9s on this step")
        self.assertEqual(stall_out(12500), "Still working… 12s on this step")
        self.assertEqual(stall_out(48000), "Still working… 48s on this step")
        src = APP_JS.read_text(encoding="utf-8")
        self.assertIn("if (quietMs <= STALL_AFTER_MS) return 'Working';", src)
        self.assertIn("const STALL_AFTER_MS = 8000;", src)


if __name__ == "__main__":
    unittest.main()
