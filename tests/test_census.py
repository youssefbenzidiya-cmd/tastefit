"""Census geocoder + ACS 5-year. No live network: every fetch is injected."""
from pathlib import Path
import unittest

import census


WICKER_TRACT = {
    "result": {"geographies": {"Census Tracts": [{
        "GEOID": "17031240100", "NAME": "Census Tract 2401, Cook County, Illinois",
        "STATE": "17", "COUNTY": "031", "TRACT": "240100", "BASENAME": "2401",
    }]}}
}
ACS = {
    "release": {"name": "ACS 2023 5-year"},
    "data": {"14000US17031240100": {
        "B19013": {"estimate": {"B19013001": 87234}},
        "B25064": {"estimate": {"B25064001": 1650}},
        "B01003": {"estimate": {"B01003001": 4120}},
        "B01002": {"estimate": {"B01002001": 32.1}},
        "B17001": {"estimate": {"B17001001": 4000, "B17001002": 560}},
        "B25003": {"estimate": {"B25003002": 800, "B25003003": 1200}},
    }},
}


class CensusCard(unittest.TestCase):
    def test_wicker_park_style_tract_parses_income_rent_and_poverty(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if "geocoder" in url:
                self.assertIn("x=-87.6796", url)
                self.assertIn("y=41.9088", url)
                return WICKER_TRACT
            self.assertIn("14000US17031240100", url)
            self.assertIn("censusreporter.org", url)
            return ACS

        card = census.card(41.9088, -87.6796, fetch=fetch)
        self.assertEqual(card["geoid"], "17031240100")
        self.assertEqual(card["median_income"], 87234)
        self.assertEqual(card["median_gross_rent"], 1650)
        self.assertEqual(card["population"], 4120)
        self.assertAlmostEqual(card["median_age"], 32.1)
        self.assertAlmostEqual(card["poverty_rate"], 0.14)
        self.assertEqual(card["vintage"], "ACS 2023 5-year")
        self.assertEqual(len(calls), 2)

    def test_acs_sentinel_is_missing_not_a_negative_rent(self):
        def fetch(url):
            if "geocoder" in url:
                return WICKER_TRACT
            payload = {"release": {"name": "ACS 2023 5-year"}, "data": {"14000US17031240100": {
                "B19013": {"estimate": {"B19013001": 87234}},
                "B25064": {"estimate": {"B25064001": -666666666}},
            }}}
            return payload

        card = census.card(41.91, -87.68, fetch=fetch)
        self.assertIsNone(card["median_gross_rent"])
        self.assertEqual(card["median_income"], 87234)

    def test_no_tract_is_none(self):
        self.assertIsNone(census.card(0, 0, fetch=lambda url: {"result": {"geographies": {}}}))

    def test_acs_failure_still_returns_the_tract(self):
        def fetch(url):
            if "geocoder" in url:
                return WICKER_TRACT
            raise RuntimeError("acs down")

        card = census.card(41.91, -87.68, fetch=fetch)
        self.assertEqual(card["geoid"], "17031240100")
        self.assertIsNone(card.get("median_income"))

    def test_api_key_is_only_from_the_environment(self):
        src = Path(census.__file__).read_text(encoding="utf-8")
        self.assertIn("CENSUS_API_KEY", src)
        self.assertNotIn("sk-", src)

    def test_live_fetch_is_an_in_memory_lru(self):
        self.assertTrue(hasattr(census.fetch_live, "cache_info"))
        census.fetch_live.cache_clear()
        self.assertEqual(census.fetch_live.cache_info().currsize, 0)
