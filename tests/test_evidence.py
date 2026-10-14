"""The evidence page quotes the committed files and escapes them."""
import unittest

import evidence


class Page(unittest.TestCase):
    def test_negative_results_and_the_links_are_on_the_page(self):
        html = evidence.page()
        self.assertIn("No measurable gain", html)
        self.assertIn("Heckman (1979)", html)
        self.assertIn("not defined", html.lower())
        self.assertIn('href="/ledger"', html)
        self.assertIn("Baseline: ignores neighbours", html)
        self.assertIn("Gale and Shapley (1962)", html)
        self.assertIn("mcp_server.py", html)
        self.assertNotIn("MCP-DEMO.md", html)
        self.assertIn("$0", html)
        self.assertIn("not a vetted address", html)
        self.assertIn("Vovk", html)
        self.assertIn("Test before you lease", html)
        self.assertIn("no interest and no discounting", html)

    def test_values_are_escaped(self):
        html = evidence.page(
            chicago={"methods": {}, "qloo_minus_prior_ci90": ["<script>"]},
            tgi={"methods": {"qloo": {"hit_at_k": 0.29, "spearman": 0.02, "survivors": 31}}},
            bias={"heckman": {"reason": "A & B <script>"}, "methods": {"prior": {}}, "conformal": {}},
            ledger_doc={"batches": [{"concept": "<script>", "area": "X", "verdict": "unscored",
                                     "check_on": "2026-11-17"}]},
        )
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("A &amp; B", html)
        self.assertIn("29%", html)

    def test_no_gain_is_the_committed_interval(self):
        html = evidence.page()
        self.assertIn("Qloo's top-3 hit rate is 29%", html)
        self.assertIn("citywide baseline is 68%", html)
        self.assertIn("\u221266 to \u22124 points", html)
        self.assertIn("\u221236 to \u22126 points", html)

    def test_a_positive_interval_is_not_called_no_gain(self):
        html = evidence.page(
            chicago={"methods": {"qloo": {"hit_at_k": 0.8}, "prior": {"hit_at_k": 0.4}},
                     "qloo_minus_prior_ci90": [0.1, 0.5]},
            tgi={"methods": {"qloo": {"hit_at_k": 0.8, "spearman": 0, "survivors": 1},
                             "prior": {"hit_at_k": 0.4, "spearman": 0, "survivors": 1},
                             "tgi": {"hit_at_k": 0.9, "spearman": 0, "survivors": 1}},
                  "tgi_minus_prior_ci90": [0.2, 0.6]},
            bias={},
            ledger_doc={"batches": [{}]},
        )
        self.assertNotIn("No measurable gain", html)
        self.assertIn("do not support a gain claim", html)


if __name__ == "__main__":
    unittest.main()
