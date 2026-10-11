"""/scan and /scan/unit against tiny synthetic packs (invented numbers, not real data). No network."""
import gzip
import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.pop("QLOO_API_KEY", None)
import app  # noqa: E402
import cityscan_breakeven as be  # noqa: E402
import scan  # noqa: E402

GRID = {"bbox": [10.0, 20.0, 10.02, 20.02], "rows": 2, "cols": 2, "cell_m": 1000, "dlat": 0.01, "dlon": 0.01}
QUALITY_OK = {"gate_passed": True, "data_confidence_food": 0.6, "threshold": 0.4, "threshold_note": "synthetic",
              "reason": None}
TOP = [{"id": k, "neighborhood": f"Synthetic {k}", "opportunity": {"point": 90 - k}} for k in range(30)]
CEILING = [{"group": "restaurant", "label_en": "restaurants", "rent_max": 8000.0, "ci90": [7000.0, 9000.0],
            "rent_max_80": 7500.0},
           {"group": "bar_cafe", "label_en": "bars and cafés", "rent_max": 300.0, "ci90": [-200.0, 900.0],
            "rent_max_80": -150.0}]


def write(dirpath, slug, doc):
    with gzip.open(Path(dirpath) / f"{slug}.json.gz", "wt", encoding="utf-8") as f:
        json.dump(doc, f)


def base(slug, name, quality):
    return {"slug": slug, "city": {"name": name}, "grid": GRID, "study": [0, 1, 2], "quality": quality,
            "attribution": "© OpenStreetMap contributors (ODbL 1.0)", "built_at": "2026-10-11T00:00:00+00:00",
            "scans": [], "units": None}


class ScanRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        good = base("synthville", "Synthville", QUALITY_OK)
        good["scans"] = [{"concept": "specialty coffee", "label": "coffee shops", "fitted": True, "n_presences": 50,
                          "auc_cv": 0.8, "auc_ci95": [0.7, 0.9], "ranking": "synthetic", "top": TOP, "limits": []}]
        doomed = [CEILING[0] | {"rent_max": -2080.0, "ci90": [-2500.0, -1500.0]}]
        good["units"] = {"mode": "calibrated", "currency": "EUR", "cells": {"0": doomed, "1": CEILING}}
        write(cls.tmp.name, "synthville", good)
        uncal = base("plainville", "Plainville", QUALITY_OK)
        write(cls.tmp.name, "plainville", uncal)
        thin = base("thinville", "Thinville", {"gate_passed": False, "data_confidence_food": 0.1, "threshold": 0.4,
                                               "threshold_note": "synthetic", "reason": "Map data is too incomplete."})
        write(cls.tmp.name, "thinville", thin)
        cls.packs = mock.patch.object(scan, "PACKS", Path(cls.tmp.name))
        cls.packs.start()
        scan._load.cache_clear()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.packs.stop()
        scan._load.cache_clear()
        cls.tmp.cleanup()

    def setUp(self):
        p = mock.patch.object(app, "scan_limiter", app.RateLimiter(1000, 600))
        p.start()
        self.addCleanup(p.stop)

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, r.headers, r.read().decode()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers, e.read().decode()

    def test_scan_returns_ranked_cells_capped_at_top(self):
        code, headers, body = self.get("/scan?city=Synthville&concept=Specialty%20Coffee&top=3")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        d = json.loads(body)
        self.assertEqual([t["id"] for t in d["top"]], [0, 1, 2])
        self.assertIn("OpenStreetMap", d["attribution"])

    def test_top_defaults_to_10_and_caps_at_25(self):
        self.assertEqual(len(json.loads(self.get("/scan?city=synthville&concept=coffee%20shops")[2])["top"]), 10)
        self.assertEqual(len(json.loads(self.get("/scan?city=synthville&concept=coffee%20shops&top=100")[2])["top"]),
                         25)

    def test_unknown_city_is_404_and_names_it(self):
        for path in ("/scan?city=Atlantis&concept=x", "/scan/unit?city=Atlantis&lat=1&lon=1"):
            code, _, body = self.get(path)
            self.assertEqual(code, 404)
            self.assertIn("Atlantis", body)

    def test_unscanned_concept_is_404_and_lists_scanned_ones(self):
        code, _, body = self.get("/scan?city=synthville&concept=sushi")
        self.assertEqual(code, 404)
        self.assertIn("specialty coffee", body)

    def test_missing_or_invalid_inputs_are_400(self):
        for path in ("/scan?city=synthville", "/scan?concept=x", "/scan?city=synthville&concept=x&top=lots",
                     "/scan/unit?city=synthville&lat=10", "/scan/unit?city=synthville&lat=north&lon=20",
                     "/scan/unit?city=synthville&lat=95&lon=20"):
            code, headers, _ = self.get(path)
            self.assertEqual(code, 400, path)
            self.assertTrue(headers["Content-Type"].startswith("text/plain"))

    def test_data_thin_city_gives_reason_and_no_numbers(self):
        for path in ("/scan?city=Thinville&concept=specialty%20coffee", "/scan/unit?city=thinville&lat=10.005&lon=20.005"):
            code, _, body = self.get(path)
            d = json.loads(body)
            self.assertEqual((code, d["data_thin"]), (200, True))
            self.assertEqual(d["reason"], "Map data is too incomplete.")
            self.assertNotIn("top", d)
            self.assertNotIn("rent_ceiling", d)

    def test_unit_gives_rent_ceiling_per_tenant_type(self):
        code, _, body = self.get("/scan/unit?city=synthville&lat=10.005&lon=20.015")
        d = json.loads(body)
        self.assertEqual((code, d["cell"], d["calibrated"]), (200, 1, True))
        self.assertEqual(d["rent_ceiling"][0]["rent_max"], 8000.0)

    def test_negative_ceiling_is_shown_as_not_viable(self):
        d = json.loads(self.get("/scan/unit?city=synthville&lat=10.005&lon=20.005")[2])
        row = d["rent_ceiling"][0]
        self.assertEqual((row["viable"], row["rent_max"], row["ci90"], row["rent_max_80"]), (False, None, None, None))
        self.assertIn("Not viable at any rent", row["note"])

    def test_no_negative_euro_figure_reaches_the_client(self):
        for lat, lon in ((10.005, 20.005), (10.005, 20.015)):
            d = json.loads(self.get(f"/scan/unit?city=synthville&lat={lat}&lon={lon}")[2])
            for row in d["rent_ceiling"]:
                for v in [row["rent_max"], row["rent_max_80"]] + (row["ci90"] or []):
                    self.assertTrue(v is None or v >= 0, row)
        cafe = d["rent_ceiling"][1]
        self.assertEqual((cafe["ci90"], cafe["rent_max_80"]), ([0.0, 900.0], None))

    def test_study_cell_without_rows_says_so(self):
        d = json.loads(self.get("/scan/unit?city=synthville&lat=10.015&lon=20.005")[2])
        self.assertIsNone(d["rent_ceiling"])
        self.assertIn("No rent ceiling", d["note"])

    def test_uncalibrated_city_never_borrows_another_citys_ceiling(self):
        d = json.loads(self.get("/scan/unit?city=plainville&lat=10.005&lon=20.005")[2])
        self.assertEqual((d["calibrated"], d["rent_ceiling"]), (False, None))
        self.assertIn("not calibrated", d["note"])

    def test_point_outside_scanned_area_is_400(self):
        for lat, lon in ((11.0, 20.005), (10.015, 20.015)):  # outside the bbox; inside it but not a study cell
            code, _, body = self.get(f"/scan/unit?city=synthville&lat={lat}&lon={lon}")
            self.assertEqual(code, 400)
            self.assertIn("outside the scanned area", body)

    def test_path_characters_never_reach_the_filesystem(self):
        for city in ("../app", "..%2F..%2Fetc%2Fpasswd", "synthville%2F..%2Fthinville", "%00"):
            code, _, _ = self.get(f"/scan?city={city}&concept=x")
            self.assertIn(code, (400, 404))
        self.assertEqual(scan.slugify("../../etc/passwd"), "etc-passwd")

    def test_no_network_call_while_serving(self):
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
                mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network")):
            self.assertEqual(scan.scan("synthville", "specialty coffee")[0], 200)
            self.assertEqual(scan.unit("synthville", "10.005", "20.005")[0], 200)


class ShippedPacks(unittest.TestCase):
    def test_shipped_packs_have_quality_and_attribution(self):
        scan._load.cache_clear()
        for slug in scan.available():
            p = scan._load(slug)
            self.assertIn("gate_passed", p["quality"])
            self.assertIn("OpenStreetMap", p["attribution"])
            if not p["quality"]["gate_passed"]:
                self.assertEqual((p["scans"], p["units"]), ([], None))

    def test_shipped_rent_ceilings_carry_their_calibration_currency(self):
        scan._load.cache_clear()
        shipped = {s: scan._load(s)["units"] for s in scan.available()}
        self.assertEqual({s for s, u in shipped.items() if u}, {"paris", "chicago"})
        for slug in ("paris", "chicago"):
            self.assertEqual(shipped[slug]["currency"], be.calibration(slug, "restaurant")["currency"])
            for row in next(iter(shipped[slug]["cells"].values())):
                calib = be.calibration(slug, be.GROUP_PROFILE[row["group"]])
                self.assertEqual(row["target_s3"], calib["official"]["s3"])


if __name__ == "__main__":
    unittest.main()
