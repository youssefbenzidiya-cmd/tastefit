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
        self.assertIn("Gale and Shapley (1962)", html)
        self.assertIn("mcp_server.py", html)
        self.assertIn("$0", html)
        self.assertIn("not a vetted address", html)
        self.assertIn("Vovk", html)

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


if __name__ == "__main__":
    unittest.main()
