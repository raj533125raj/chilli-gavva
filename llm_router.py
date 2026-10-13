"""LLM fallback router: FreeLLMAPI (local) → Groq → Gemini (free) → Cloudflare Workers AI.

All four use OpenAI-compatible /v1/chat/completions, all via stdlib urllib — zero third-party deps.

Usage:
    import llm_router
    text = llm_router.chat("write a headline about Indian spices")
    # Returns str on success, None if all providers failed.

Environment variables (all optional — missing key = skip that provider):
    FREELLMAPI_KEY       freellmapi local router token (starts freellmapi-). Run locally: freellmapi.co
    FREELLMAPI_BASE_URL  Override local router URL (default: http://localhost:3001/v1)
    GROQ_API_KEY         Groq console.groq.com — free, no card
    GEMINI_API_KEY       aistudio.google.com/apikey — free, no card
    CF_API_TOKEN         Cloudflare API token with Workers AI read
    CF_ACCOUNT_ID        Cloudflare account ID (Settings → scroll down)

Behaviour:
    - FreeLLMAPI: pools 7B+ free tokens/month from 34 providers — used first when running locally
    - Groq: llama-3.1-8b-instant, retries once on 429
    - Gemini: gemini-1.5-flash (free tier 15 RPM / 1M TPD), uses native REST API
    - Cloudflare: @cf/meta/llama-3.1-8b-instruct, free 10k neurons/day
    - On any non-retryable error: skip to next provider
    - Returns None if every provider fails or all keys are absent
"""
import json
import os
import time
import urllib.error
import urllib.request

import guardrails

_TIMEOUT = 25  # seconds per request


# ---------------------------------------------------------------------------
# Provider 0: FreeLLMAPI (local router — pools 7B+ tokens/month free)
# Install: freellmapi.co | Run locally and set FREELLMAPI_KEY env var
# ---------------------------------------------------------------------------
def _freellmapi(prompt: str) -> str | None:
    api_key = os.environ.get("FREELLMAPI_KEY", "")
    if not api_key or not api_key.startswith("freellmapi-"):
        return None

    base_url = os.environ.get("FREELLMAPI_BASE_URL", "http://localhost:3001/v1")
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": "auto",  # let the router pick the best free model
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers=headers
        )
        guardrails.record_call()
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"].strip() or None
    except Exception:
        # Silently skip — localhost not running is expected in cloud/CI
        return None


# ---------------------------------------------------------------------------
# Provider 1: Groq
# ---------------------------------------------------------------------------
def _groq(prompt: str) -> str | None:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key or api_key.startswith("YOUR_"):
        return None

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }

    for attempt in (1, 2):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(), headers=headers
            )
            guardrails.record_call()
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
                return data["choices"][0]["message"]["content"].strip() or None
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:
                wait = 5.0
                try:
                    wait = min(float(e.headers.get("retry-after", "5")), 20.0)
                except (ValueError, TypeError):
                    pass
                time.sleep(wait)
                continue
            return None
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Provider 2: Google Gemini (free tier)
# ---------------------------------------------------------------------------
def _gemini(prompt: str) -> str | None:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or api_key.startswith("YOUR_"):
        return None

    # Gemini uses its own REST format (not OpenAI-compatible at the native endpoint)
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-1.5-flash:generateContent?key={api_key}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1024},
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        guardrails.record_call()
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            # Response shape: data["candidates"][0]["content"]["parts"][0]["text"]
            return (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
                .strip()
                or None
            )
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # Gemini free tier is 15 RPM — skip, don't wait
            pass
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Provider 3: Cloudflare Workers AI (free 10k neurons/day)
# ---------------------------------------------------------------------------
def _cloudflare(prompt: str) -> str | None:
    token = os.environ.get("CF_API_TOKEN", "")
    account_id = os.environ.get("CF_ACCOUNT_ID", "")
    if not token or not account_id or token.startswith("YOUR_"):
        return None

    # Cloudflare uses OpenAI-compatible endpoint
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
        "/ai/run/@cf/meta/llama-3.1-8b-instruct"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
    }
    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers=headers
        )
        guardrails.record_call()
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            # Shape: {"result": {"response": "..."}, "success": true}
            return (data.get("result") or {}).get("response", "").strip() or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
_PROVIDERS = [
    ("FreeLLMAPI", _freellmapi),
    ("Groq", _groq),
    ("Gemini", _gemini),
    ("Cloudflare", _cloudflare),
]


def chat(prompt: str, *, db_path=None) -> str | None:
    """Try each provider in order; return first non-None result.

    Respects guardrails.can_call_llm() before the first attempt.
    Returns None if all providers fail or daily cap is hit.
    """
    if not guardrails.can_call_llm(db_path):
        return None

    _mod_globals = globals()
    for name, fn_name in [("FreeLLMAPI", "_freellmapi"), ("Groq", "_groq"), ("Gemini", "_gemini"), ("Cloudflare", "_cloudflare")]:
        fn = _mod_globals[fn_name]
        try:
            result = fn(prompt)
        except Exception:
            result = None
        if result:
            return result

    return None


def provider_status() -> list[dict]:
    """Return which providers have their env vars configured (does NOT make network calls)."""
    status = []
    for name, _ in _PROVIDERS:
        if name == "FreeLLMAPI":
            key = os.environ.get("FREELLMAPI_KEY", "")
            configured = bool(key and key.startswith("freellmapi-"))
        elif name == "Groq":
            key = os.environ.get("GROQ_API_KEY", "")
            configured = bool(key and not key.startswith("YOUR_"))
        elif name == "Gemini":
            key = os.environ.get("GEMINI_API_KEY", "")
            configured = bool(key and not key.startswith("YOUR_"))
        elif name == "Cloudflare":
            tok = os.environ.get("CF_API_TOKEN", "")
            acc = os.environ.get("CF_ACCOUNT_ID", "")
            configured = bool(tok and acc and not tok.startswith("YOUR_"))
        else:
            configured = False
        status.append({"provider": name, "configured": configured})
    return status
