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
        err = urllib.error.HTTPError("u", 503, "unavailable", {}, io.BytesIO(b"fake-gemini-key down"))
        fake = FakeOpen(err, {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]})
        with mock.patch("llm.urllib.request.urlopen", fake), self.assertLogs("tastefit.llm", "WARNING") as logs:
            msg = llm.chat([{"role": "user", "content": "x"}], tools=[{"name": "t", "parameters": {}}])
        (g_req, g_body, g_timeout), (lite_req, lite_body, lite_timeout) = fake.requests
        self.assertTrue(g_req.full_url.startswith(llm.GEMINI_BASE_URL))
        self.assertEqual(g_body["model"], "gemini-3.8-flash")
        self.assertEqual(g_timeout, 8)
        self.assertEqual(g_body["reasoning_effort"], "low")
        self.assertEqual(g_req.get_header("Authorization"), "Bearer fake-gemini-key")
        self.assertEqual(lite_body["model"], "gemini-3.1-flash-lite")
        self.assertEqual(lite_timeout, 20)
        self.assertEqual(lite_body["tools"][0]["type"], "function")
        self.assertEqual(msg["provider"], "gemini-lite")
        self.assertNotIn("fake-gemini-key", " ".join(logs.output))

    def test_both_gemini_models_fail_then_community(self):
        os.environ["GEMINI_API_KEY"] = "fake-gemini-key"
        err = urllib.error.HTTPError("u", 503, "unavailable", {}, io.BytesIO(b""))
        fake = FakeOpen(err, TimeoutError(), {"role": "assistant", "content": "ok"})
        with mock.patch("llm.urllib.request.urlopen", fake):
            msg = llm.chat([{"role": "user", "content": "x"}])
        self.assertEqual([body["model"] for _, body, _ in fake.requests[:2]],
                         ["gemini-3.8-flash", "gemini-3.1-flash-lite"])
        self.assertEqual([t for _, _, t in fake.requests], [8, 20, llm.COMMUNITY_AFTER_GEMINI_TIMEOUT])
        self.assertEqual(msg["provider"], "community")

    def test_pinned_gemini_model_skips_the_chain(self):
        os.environ["GEMINI_API_KEY"] = "fake-gemini-key"
        os.environ["GEMINI_MODEL"] = "gemini-3.1-flash-lite"
        self.assertEqual(llm.provider_names(), ["gemini", "community"])
        fake = FakeOpen({"role": "assistant", "content": "ok"})
        with mock.patch("llm.urllib.request.urlopen", fake):
            llm.chat([{"role": "user", "content": "x"}])
        self.assertEqual(fake.requests[0][1]["model"], "gemini-3.1-flash-lite")

    def test_all_failures_raise_without_leaking_keys(self):
        os.environ["GEMINI_API_KEY"] = "fake-gemini-key"
        fake = FakeOpen(TimeoutError(), TimeoutError(), {"role": "assistant", "content": ""})
        with mock.patch("llm.urllib.request.urlopen", fake), self.assertRaises(llm.LLMUnavailable) as ctx:
            llm.chat([{"role": "user", "content": "x"}], timeout=5)
        self.assertEqual(str(ctx.exception), "gemini: TimeoutError; gemini-lite: TimeoutError; community: ValueError")
        self.assertEqual([t for _, _, t in fake.requests], [5, 5, 5])

    def test_custom_endpoint_and_model(self):
        os.environ.update(LLM_BASE_URL="https://example.test/v1/", LLM_MODEL="m1")
        fake = FakeOpen({"role": "assistant", "content": "ok"})
        with mock.patch("llm.urllib.request.urlopen", fake):
            llm.chat([])
        req, body, timeout = fake.requests[0]
        self.assertEqual((req.full_url, body["model"], timeout), ("https://example.test/v1/chat/completions", "m1", 150))


if __name__ == "__main__":
    unittest.main()
