"""Provider chain of llm.chat with a fake urlopen: no network, no real keys."""
import io
import json
import os
import unittest
import urllib.error
from unittest import mock

import llm


def response(message):
    return io.BytesIO(json.dumps({"choices": [{"message": message}]}).encode())


class FakeOpen:
    def __init__(self, *outcomes):
        self.outcomes, self.requests = list(outcomes), []

    def __call__(self, req, timeout=None):
        self.requests.append((req, json.loads(req.data), timeout))
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return response(out)


class ChainTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {}, clear=False)
        p.start()
        self.addCleanup(p.stop)
        for k in ("GEMINI_API_KEY", "GEMINI_MODEL", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
            os.environ.pop(k, None)

    def test_without_gemini_key_only_the_community_endpoint_is_used(self):
        self.assertEqual(llm.provider_names(), ["community"])
        fake = FakeOpen({"role": "assistant", "content": "hi"})
        with mock.patch("llm.urllib.request.urlopen", fake):
            msg = llm.chat([{"role": "user", "content": "x"}])
        req, body, _ = fake.requests[0]
        self.assertEqual(req.full_url, llm.COMMUNITY_BASE_URL + "/chat/completions")
        self.assertNotIn("tools", body)
        self.assertEqual(msg["provider"], "community")

    def test_gemini_first_then_fallback_on_error(self):
        os.environ["GEMINI_API_KEY"] = "fake-gemini-key"
        err = urllib.error.HTTPError("u", 429, "quota", {}, io.BytesIO(b"fake-gemini-key quota"))
        fake = FakeOpen(err, {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]})
        with mock.patch("llm.urllib.request.urlopen", fake), self.assertLogs("tastefit.llm", "WARNING") as logs:
            msg = llm.chat([{"role": "user", "content": "x"}], tools=[{"name": "t", "parameters": {}}])
        (g_req, g_body, _), (c_req, c_body, _) = fake.requests
        self.assertTrue(g_req.full_url.startswith(llm.GEMINI_BASE_URL))
        self.assertEqual(g_body["model"], "gemini-3.8-flash")
        self.assertEqual(g_body["reasoning_effort"], "low")
        self.assertEqual(g_req.get_header("Authorization"), "Bearer fake-gemini-key")
        self.assertEqual(c_body["tools"][0]["type"], "function")
        self.assertEqual(msg["provider"], "community")
        self.assertNotIn("fake-gemini-key", " ".join(logs.output))

    def test_all_failures_raise_without_leaking_keys(self):
        os.environ["GEMINI_API_KEY"] = "fake-gemini-key"
        fake = FakeOpen(TimeoutError(), {"role": "assistant", "content": ""})
        with mock.patch("llm.urllib.request.urlopen", fake), self.assertRaises(llm.LLMUnavailable) as ctx:
            llm.chat([{"role": "user", "content": "x"}], timeout=5)
        self.assertEqual(str(ctx.exception), "gemini: TimeoutError; community: ValueError")
        self.assertEqual([t for _, _, t in fake.requests], [5, 5])

    def test_custom_endpoint_and_model(self):
        os.environ.update(LLM_BASE_URL="https://example.test/v1/", LLM_MODEL="m1")
        fake = FakeOpen({"role": "assistant", "content": "ok"})
        with mock.patch("llm.urllib.request.urlopen", fake):
            llm.chat([])
        req, body, timeout = fake.requests[0]
        self.assertEqual((req.full_url, body["model"], timeout), ("https://example.test/v1/chat/completions", "m1", 150))


if __name__ == "__main__":
    unittest.main()
