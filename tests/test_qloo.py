"""qloo.exec_workflow against a fake `qloo` executable: no network, no real key."""
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import qloo

FAKE_HARNESS = f"""#!{sys.executable}
import json, os, sys
_, cmd, op, flag, raw = sys.argv
if op == "fail":
    sys.stderr.write("harness failed: bad input")
    sys.exit(2)
if op == "garbage":
    print("not json")
    sys.exit(0)
print(json.dumps({{"cmd": cmd, "op": op, "flag": flag, "input": json.loads(raw),
                  "base": os.environ.get("QLOO_BASE_URL"), "trusted": os.environ.get("QLOO_TRUSTED_BASE_URL")}}))
"""


class ExecWorkflow(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        exe = Path(tmp.name) / "qloo"
        exe.write_text(FAKE_HARNESS)
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        for p in (mock.patch.object(qloo, "QLOO_BIN", str(exe)),
                  mock.patch.dict(os.environ, {"QLOO_API_KEY": "fake-test-key"})):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("QLOO_BASE_URL", None)
        os.environ.pop("QLOO_TRUSTED_BASE_URL", None)

    def test_runs_exec_with_json_input_and_drops_empty_params(self):
        out = qloo.exec_workflow("recommend", {"a": 1, "b": None, "c": [], "d": "", "e": ["x"]})
        self.assertEqual((out["cmd"], out["op"], out["flag"]), ("exec", "recommend", "--input"))
        self.assertEqual(out["input"], {"a": 1, "e": ["x"]})
        self.assertEqual(out["base"], "https://hackathon.api.qloo.com")
        self.assertEqual(out["trusted"], out["base"])

    def test_helpers_send_only_concept_and_place(self):
        self.assertEqual(qloo.find_tags("wine bar")["input"], {"query": "wine bar", "limit": 8})
        rec = qloo.recommend_places("Brooklyn", ["urn:tag:x"])["input"]
        self.assertEqual(rec, {"target_type": "place", "signal_tags": ["urn:tag:x"], "filter_location": "Brooklyn", "limit": 12})
        self.assertEqual(qloo.area_taste("Brooklyn")["input"]["signal_location"], "Brooklyn")
        self.assertEqual(qloo.where_popular("Cafe", "Brooklyn")["op"], "where_popular")

    def test_stderr_becomes_runtime_error(self):
        with self.assertRaisesRegex(RuntimeError, "bad input"):
            qloo.exec_workflow("fail", {})

    def test_non_json_output_becomes_runtime_error(self):
        with self.assertRaisesRegex(RuntimeError, "non-JSON"):
            qloo.exec_workflow("garbage", {})

    def test_missing_key_fails_before_running(self):
        os.environ.pop("QLOO_API_KEY")
        with mock.patch.object(qloo.subprocess, "run") as run, self.assertRaisesRegex(RuntimeError, "QLOO_API_KEY"):
            qloo.exec_workflow("recommend", {})
        run.assert_not_called()


class Cache(unittest.TestCase):
    def test_lru_evicts_oldest_and_expires(self):
        now = [0.0]
        c = qloo.LRUCache(maxsize=2, ttl=10, clock=lambda: now[0])
        c.put("a", 1)
        c.put("b", 2)
        c.get("a")
        c.put("c", 3)
        self.assertEqual((c.get("a"), c.get("b"), c.get("c")), (1, None, 3))
        now[0] = 10
        self.assertIsNone(c.get("a"))

    def test_exec_cached_caches_only_successes(self):
        results = [{"status": "error"}, {"status": "ok", "results": [1]}]
        with mock.patch.object(qloo, "CACHE", qloo.LRUCache()), \
                mock.patch.object(qloo, "exec_workflow", side_effect=lambda *a: results.pop(0)) as ex:
            self.assertEqual(qloo.exec_cached("rank", {"b": 1, "a": 2}), ({"status": "error"}, False))
            self.assertEqual(qloo.exec_cached("rank", {"a": 2, "b": 1})[1], False)
            self.assertEqual(qloo.exec_cached("rank", {"a": 2, "b": 1}), ({"status": "ok", "results": [1]}, True))
        self.assertEqual(ex.call_count, 2)


class Builders(unittest.TestCase):
    """Every tenant-match tool sends only concepts, places and Qloo ids, in the harness's documented shape."""

    def test_builders(self):
        self.assertEqual(qloo.p_area_taste("Wicker Park, Chicago"),
                         ("recommend", {"target_type": "place", "signal_location": "Wicker Park, Chicago",
                                        "filter_location": "Wicker Park, Chicago", "limit": 10, "explain": False}))
        op, p = qloo.p_concept_places("Area", "City", "urn:tag:x", limit=5)
        self.assertEqual((op, p["include_tags"], p["signal_location"], p["filter_location"], p["limit"]),
                         ("recommend", ["urn:tag:x"], "Area", "City", 5))
        self.assertEqual(qloo.p_entity_tags([str(i) for i in range(12)])[1]["entities"], [str(i) for i in range(10)])
        self.assertEqual(qloo.p_demographics("E1"), ("audience_demographics", {"entity": "E1", "entity_type": "place"}))
        self.assertEqual(qloo.p_find_tags("ramen"), ("find_tags", {"query": "ramen", "limit": 6}))
        self.assertEqual(qloo.p_rank(["a", "b"], "Area"),
                         ("rank", {"options": ["a", "b"], "option_type": "place", "signal_location": "Area"}))
        self.assertEqual(qloo.p_compare(["a"], ["b"]),
                         ("compare_audiences", {"group_a": ["a"], "group_b": ["b"], "limit": 8}))
        self.assertEqual(qloo.p_trends("CAVA", "2026-04-01", "2026-10-01"),
                         ("trends", {"entities": ["CAVA"], "entity_type": "brand", "start_date": "2026-04-01",
                                     "end_date": "2026-10-01", "limit": 20}))
        self.assertEqual(qloo.p_where_popular("E1", "Chicago"),
                         ("where_popular", {"entity": "E1", "entity_type": "place", "within": "Chicago", "limit": 8}))


if __name__ == "__main__":
    unittest.main()
