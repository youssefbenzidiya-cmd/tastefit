"""MCP stdio server: JSON-RPC framing, tool listing and dispatch, secret redaction. No Qloo or LLM calls."""
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

import mcp_server as m

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rpc(method, params=None, mid=1):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return m.handle(msg)


def tool(name, args):
    r = rpc("tools/call", {"name": name, "arguments": args})["result"]
    return json.loads(r["content"][0]["text"]), r["isError"]


class ProtocolTest(unittest.TestCase):
    def test_initialize_negotiates_version(self):
        r = rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}})["result"]
        self.assertEqual(r["protocolVersion"], "2025-03-26")
        self.assertEqual(r["serverInfo"]["name"], "tastefit")
        self.assertIn("tools", r["capabilities"])
        self.assertEqual(rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"], m.PROTOCOL_VERSIONS[0])

    def test_tools_list(self):
        tools = rpc("tools/list")["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["tenant_match", "city_scan", "city_scan_unit"])
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_errors_and_notifications(self):
        self.assertEqual(rpc("nope")["error"]["code"], -32601)
        self.assertEqual(rpc("tools/call", {"name": "nope"})["error"]["code"], -32602)
        self.assertEqual(rpc("tools/call", {"name": "city_scan", "arguments": [1]})["error"]["code"], -32602)
        self.assertIsNone(m.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(m.handle({"id": 3, "method": "ping"})["error"]["code"], -32600)
        self.assertEqual(rpc("ping")["result"], {})

    def test_serve_frames_one_reply_per_request(self):
        out = io.StringIO()
        m.serve(io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\nnot json\n'
                            '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'), out)
        lines = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual([x.get("id") for x in lines], [1, None])
        self.assertEqual(lines[1]["error"]["code"], -32700)

    def test_stdout_carries_only_protocol(self):
        env = {k: v for k, v in os.environ.items() if k != "QLOO_API_KEY"}
        req = "".join(json.dumps(x) + "\n" for x in [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
        p = subprocess.run([sys.executable, "mcp_server.py"], input=req, capture_output=True, text=True, cwd=ROOT,
                           env=env, timeout=60)
        replies = [json.loads(x) for x in p.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])


class ToolTest(unittest.TestCase):
    def test_city_scan_tools_use_the_packs(self):
        body, err = tool("city_scan", {"city": "Atlantis", "concept": "x"})
        self.assertTrue(err)
        self.assertIn("error", body)
        body, err = tool("city_scan_unit", {"city": "Chicago", "lat": "x", "lon": 1})
        self.assertTrue(err)
        with mock.patch("mcp_server.scan.unit", return_value=(200, {"calibrated": True, "rent_ceiling": []})):
            body, err = tool("city_scan_unit", {"city": "Chicago", "lat": 41.9, "lon": -87.68})
        self.assertFalse(err)
        self.assertTrue(body["calibrated"])

    def test_tenant_match_collects_the_run(self):
        events = [("unit", {"area": "Wicker Park"}), ("step", {"workflow": "rank", "purpose": "p", "input": {"x": 1}}),
                  ("shortlist", {"concepts": [{"concept": "ramen", "call": "GO"}]}), ("baseline", {"ledger": {}}),
                  ("memo", {"markdown": "# Memo", "author": "template"}), ("budget", {"qloo_calls": 20})]
        with mock.patch("mcp_server.tenant.run", return_value=iter(events)) as run:
            body, err = tool("tenant_match", {"lat": 41.9, "lon": -87.68, "asking_rent": 4000})
        run.assert_called_once_with(None, None, 41.9, -87.68, 4000)
        self.assertFalse(err)
        self.assertEqual(body["steps"], [{"workflow": "rank", "purpose": "p"}])  # request bodies are not echoed
        self.assertEqual((body["memo"], body["budget"]["qloo_calls"]), ("# Memo", 20))

    def test_tenant_match_errors(self):
        body, err = tool("tenant_match", {"city": "Paris"})
        self.assertTrue(err)
        with mock.patch("mcp_server.tenant.run", return_value=iter([("error", "Qloo returned no places")])):
            body, err = tool("tenant_match", {"city": "Paris", "location": "Nowhere"})
        self.assertEqual((body, err), ({"error": "Qloo returned no places"}, True))
        with mock.patch("mcp_server.tenant.run", side_effect=RuntimeError("boom")):
            r = rpc("tools/call", {"name": "tenant_match", "arguments": {"lat": 1, "lon": 2}})["result"]
        self.assertTrue(r["isError"])
        self.assertNotIn("boom", r["content"][0]["text"])

    def test_key_never_appears_in_output(self):
        leak = [("memo", {"markdown": "key secret-test-key-123", "author": "x"})]
        with mock.patch.dict("os.environ", {"QLOO_API_KEY": "secret-test-key-123"}), \
                mock.patch("mcp_server.tenant.run", return_value=iter(leak)):
            r = rpc("tools/call", {"name": "tenant_match", "arguments": {"lat": 1, "lon": 2}})
        self.assertNotIn("secret-test-key-123", json.dumps(r))


if __name__ == "__main__":
    unittest.main()
