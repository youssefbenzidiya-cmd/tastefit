"""OpenAI-compatible chat completions with an ordered provider chain.

1. Gemini (Google AI Studio free tier) when GEMINI_API_KEY is set, through its OpenAI-compatible endpoint.
   gemini-3.8-flash is tried first with a short timeout: it often times out or returns 503. gemini-3.1-flash-lite
   is the fallback that answers. Set GEMINI_MODEL to pin a single model and skip that chain.
2. The OpenAI-compatible endpoint in LLM_BASE_URL (default: a free keyless community endpoint).
A provider that errors or times out hands over to the next one; callers keep a deterministic fallback for when
every provider fails. Keys come from the environment only and never appear in errors or logs.
A single chat stays inside the per-provider budgets below so one hung model cannot spend the Cloud Run 300 s.
"""
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("tastefit.llm")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
COMMUNITY_BASE_URL = "https://hermes.ai.unturf.com/v1"
# (name, model, timeout_s). The first model fails slowly, so its budget is short; lite is the one that answers.
GEMINI_CHAIN = (
    ("gemini", "gemini-3.8-flash", 8),
    ("gemini-lite", "gemini-3.1-flash-lite", 20),
)
COMMUNITY_TIMEOUT = 150
COMMUNITY_AFTER_GEMINI_TIMEOUT = 30


class LLMUnavailable(RuntimeError):
    """Every configured provider failed."""


def providers():
    """[(name, base_url, model, api_key, timeout_s)] in the order they are tried."""
    out = []
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        pinned = os.environ.get("GEMINI_MODEL", "").strip()
        chain = (("gemini", pinned, 20),) if pinned else GEMINI_CHAIN
        out.extend((name, GEMINI_BASE_URL, model, key, timeout_s) for name, model, timeout_s in chain)
    out.append(("community", os.environ.get("LLM_BASE_URL", COMMUNITY_BASE_URL).rstrip("/"),
                os.environ.get("LLM_MODEL", "turboderp/Qwen3.8-27B-exl3"), os.environ.get("LLM_API_KEY", "none"),
                COMMUNITY_AFTER_GEMINI_TIMEOUT if key else COMMUNITY_TIMEOUT))
    return out


def _post(base_url, model, key, messages, tools, timeout, extra):
    body = {"model": model, "messages": messages, "temperature": 0.2, **extra}
    if tools:
        body["tools"] = [{"type": "function", "function": t} for t in tools]
    req = urllib.request.Request(base_url + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        msg = json.loads(r.read())["choices"][0]["message"]
    if not isinstance(msg, dict) or not (msg.get("content") or msg.get("tool_calls")):
        raise ValueError("empty completion")
    return msg


def chat(messages, tools=None, timeout=None):
    """The first provider's assistant message ({role, content, tool_calls}); raises LLMUnavailable if all fail."""
    errors = []
    for name, base_url, model, key, default_timeout in providers():
        extra = {"reasoning_effort": "low"} if name.startswith("gemini") else {}
        # A caller may cap the wait, but never past this provider's own budget, so a dead model
        # falls through before it can use the whole Cloud Run request.
        budget = default_timeout if timeout is None else min(timeout, default_timeout)
        try:
            msg = _post(base_url, model, key, messages, tools, budget, extra)
            msg["provider"] = name
            return msg
        except urllib.error.HTTPError as e:
            errors.append(f"{name}: HTTP {e.code}")
        except Exception as e:  # noqa: BLE001  any failure hands over to the next provider
            errors.append(f"{name}: {type(e).__name__}")
        log.warning("LLM provider failed, trying the next one: %s", errors[-1])
    raise LLMUnavailable("; ".join(errors))


def provider_names():
    return [p[0] for p in providers()]
