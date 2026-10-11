"""qloo.exec_workflow against a fake `qloo` executable: no network, no real key."""
import json
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


if __name__ == "__main__":
    unittest.main()
