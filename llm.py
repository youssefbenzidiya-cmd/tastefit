"""OpenAI-compatible chat completions with an ordered provider chain.

1. Gemini (Google AI Studio free tier) when GEMINI_API_KEY is set, through its OpenAI-compatible endpoint.
2. The OpenAI-compatible endpoint in LLM_BASE_URL (default: a free keyless community endpoint).
A provider that errors or times out hands over to the next one; callers keep a deterministic fallback for when
every provider fails. Keys come from the environment only and never appear in errors or logs.
"""
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("tastefit.llm")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
COMMUNITY_BASE_URL = "https://hermes.ai.unturf.com/v1"


class LLMUnavailable(RuntimeError):
    """Every configured provider failed."""


def providers():
    """[(name, base_url, model, api_key, timeout_s)] in the order they are tried."""
    out = []
    if os.environ.get("GEMINI_API_KEY"):
        out.append(("gemini", GEMINI_BASE_URL, os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
                    os.environ["GEMINI_API_KEY"], 60))
    out.append(("community", os.environ.get("LLM_BASE_URL", COMMUNITY_BASE_URL).rstrip("/"),
                os.environ.get("LLM_MODEL", "turboderp/Qwen3.8-27B-exl3"), os.environ.get("LLM_API_KEY", "none"), 150))
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
        extra = {"reasoning_effort": "low"} if name == "gemini" else {}
        try:
            msg = _post(base_url, model, key, messages, tools, timeout or default_timeout, extra)
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
