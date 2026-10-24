import os
import re
import unittest

os.environ.pop("QLOO_API_KEY", None)
import app  # noqa: E402


class ChicagoPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = app.render_index("n").decode()
        cls.js = (app.ROOT / "static" / "app.js").read_text()

    def test_wordmark_and_chicago_only_copy(self):
        self.assertIn("TasteFit", self.page)
        self.assertIn("Who should rent this unit", self.page)
        self.assertIn("not for sale", self.page)
        self.assertIn("Chicago", self.page)
        self.assertNotIn("Rupture", self.page)
        self.assertNotIn("Broker or landlord", self.page)
        self.assertNotIn("data-mode", self.page)
        self.assertNotIn('value="paris"', self.page)
        self.assertIn("leaflet", self.page.lower())
        self.assertIn("Qloo", self.page)
        self.assertIn("Census", self.page)
        self.assertNotIn("Unclassified", self.page)
        self.assertIn("not a vetted address", self.page.lower())

    def test_concept_chips_are_the_calibrated_groups(self):
        self.assertEqual(re.findall(r'data-group="([^"]+)"', self.page),
                         ["restaurant", "fast_food", "bar_cafe"])

    def test_path_losses_live_in_the_script(self):
        self.assertNotIn('data-layer="lab"', self.page)
        for token in ("pathLosses", "not for sale", "eighty_for_sale",
                      "card.caps", "card.fx", "Least expected loss", 'class="who"',
                      "affineLosses", "matchBase", "expectedShortfall", "kellyBankroll",
                      "vasicekK", "id=\"frontier\""):
            self.assertIn(token, self.js, token)

    def test_page_asks_for_build_out(self):
        self.assertIn('id="capex"', self.page)
        self.assertIn('id="term"', self.page)
        self.assertIn('id="share"', self.page)
        self.assertIn("closed by year 3", self.js)
        self.assertIn("for sale as a share", self.page.lower())

    def test_credit_book_identities_live_in_the_script(self):
        self.assertIn("function book(", self.js)
        self.assertIn("hurdle", self.js)
        self.assertIn("guarantee", self.js)

    def test_listing_is_a_decision_not_a_fact_sheet(self):
        self.assertIn('id="belief"', self.page)
        self.assertIn('id="rent-range"', self.page)
        self.assertIn("function npvLine(", self.js)
        self.assertIn("id=\"paths\"", self.js)
        self.assertIn("card.caps", self.js)
        self.assertIn("the mean of those two", self.js)

    def test_happy_path_is_pin_census_licences_taste(self):
        self.assertIn("/ask?", self.js)
        self.assertIn("/spot?", self.js)
        self.assertIn("/taste?", self.js)
        self.assertIn("bootMap", self.js)
        self.assertIn("L.map", self.js)
        self.assertIn("pinIcon", self.js)
        self.assertIn("Reading the tract", self.js)
        self.assertIn("glance-n", self.js)
        self.assertIn("openMemo", self.js)
        self.assertIn("Print memo", self.js)
        self.assertIn("What this street loves", self.js)
        self.assertIn("youssefbenzidiya@gmail.com", self.page)
        self.assertIn("youssefbenzidiya@gmail.com", self.js)
        self.assertIn("mailto:${esc(CONTACT)}", self.js)
        self.assertIn("Who this street loves", self.js)
        self.assertIn("function isQlooPlace(", self.js)
        self.assertIn("R = b + αG", self.js)
        self.assertIn("ES 95%", self.js)
        self.assertIn("Kelly", self.js)
        self.assertIn("Vasicek", self.js)
        self.assertIn("ACS tract aggregates", self.js)
        self.assertIn("Write ${esc(CONTACT)}", self.js)
        self.assertIn("function qlooRead(", self.js)
        self.assertIn("In English", self.js)
        self.assertIn("seed 20261011", self.js)
        self.assertIn("κ = 0", self.js)
        self.assertIn("Questions on this pin", self.js)
        self.assertIn("class=\"mail-link\"", self.page)
        self.assertLess(self.js.index('data-action="memo"'), self.js.index('id="paths"'))
        self.assertIn('id="lab"', self.js)
        self.assertIn('id="memo"', self.page)
        self.assertIn('method="dialog"', self.page)
        self.assertIn('href="/walkthrough"', self.page)
        self.assertIn("const CITY = 'chicago'", self.js)
        self.assertNotIn("/scan/unit", self.js)
        self.assertIn("/tenant?", self.js)
        self.assertIn("data-action=\"agent\"", self.js)
        self.assertNotIn("/demo", self.js)
        self.assertNotIn('"/run"', self.js)
        self.assertNotIn("'/run'", self.js)

    def test_money_uses_the_response_currency(self):
        self.assertIn("Intl.NumberFormat('en', {\n  style: 'currency', currency: cur || 'USD'", self.js)
        self.assertNotIn("€", self.js)
        self.assertNotIn("&euro;", self.js)

    def test_api_text_is_escaped(self):
        for field in ("card.source", "card.note", "card.label_en"):
            self.assertIn("esc(" + field, self.js, field)
