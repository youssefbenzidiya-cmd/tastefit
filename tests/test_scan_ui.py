import os
import re
import unittest

os.environ.pop("QLOO_API_KEY", None)
import app  # noqa: E402
import scan  # noqa: E402


class ScanPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = app.render_index("n").decode()
        cls.panel = cls.page.split('<section class="scan" id="scan">')[1].split("</section>")[0]
        scripts = [s.split("</script>")[0] for s in cls.page.split('<script nonce="n">')[1:]]
        cls.script = next(s for s in scripts if "scan-form" in s)

    def test_city_options_are_the_shipped_packs(self):
        self.assertEqual(sorted(re.findall(r'<option value="([a-z0-9-]+)">', self.panel)), scan.available())

    def test_concept_options_are_scanned_everywhere(self):
        concepts = re.findall(r"<option>([^<]+)</option>", self.panel)
        self.assertEqual(len(concepts), 6)
        for slug in ("paris", "chicago"):
            self.assertEqual(concepts, [s["concept"] for s in scan.pack(slug)["scans"]])

    def test_money_uses_the_response_currency(self):
        self.assertIn("Intl.NumberFormat('en', { style: 'currency', currency: u.currency", self.script)
        self.assertNotIn("€", self.script)
        self.assertNotIn("&euro;", self.script)

    def test_calls_only_same_origin_scan_endpoints(self):
        fetched = re.findall(r"getJSON\('([^']+)'", self.script)
        self.assertEqual(sorted(fetched), ["/scan/unit?", "/scan?"])

    def test_api_text_is_escaped(self):
        for field in ("d.reason", "d.attribution", "u.note", "r.note", "r.label_en", "t.neighborhood", "why", "watch",
                      "u.market_share.note"):
            self.assertRegex(self.script, r"esc\(" + re.escape(field), field)


if __name__ == "__main__":
    unittest.main()
