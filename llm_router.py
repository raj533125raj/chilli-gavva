"""LLM fallback router: FreeLLMAPI (local) → Groq → Gemini (free) → OpenRouter (free) → Cloudflare Workers AI.

All of them use OpenAI-compatible /v1/chat/completions, all via stdlib urllib — zero third-party deps.

Usage:
    python3 llm_router.py health     # one tiny test per provider/model + one long-answer test
    import llm_router
    text = llm_router.chat("write a headline about Indian spices")          # auto size
    text = llm_router.chat(long_prompt, size="large")                         # strong model
    # Returns str on success, None if all providers failed.

Environment variables (all optional — missing key = skip that provider):
    FREELLMAPI_KEY       freellmapi local router token (starts freellmapi-). Run locally: freellmapi.co
    FREELLMAPI_BASE_URL  Override local router URL (default: http://localhost:3001/v1)
    GROQ_API_KEY         Groq console.groq.com — free, no card
    GEMINI_API_KEY       aistudio.google.com/apikey — free, no card
    GEMINI_MODEL         Optional comma list of Gemini models to try (default: gemini-3.8-flash,gemini-flash-latest,gemini-3.5-flash)
    OPENROUTER_API_KEY   openrouter.ai free account key
    OPENROUTER_MODEL     Required with OPENROUTER_API_KEY: a current free model id (ends with ":free")
    CF_API_TOKEN         Cloudflare API token with Workers AI read
    CF_ACCOUNT_ID        Cloudflare account ID (Settings → scroll down)

Behaviour:
    - FreeLLMAPI: pools 7B+ free tokens/month from 34 providers — used first when running locally
    - Groq: size routing — "small" = openai/gpt-oss-20b, "large" = openai/gpt-oss-120b
      (large falls back to small), retries once on 429
    - Gemini: OpenAI-compatible endpoint, tries GEMINI_MODEL list in order (unknown or busy model -> next)
    - OpenRouter: OpenAI-compatible, only when both key and a free model id are set
    - Cloudflare: @cf/meta/llama-3.1-8b-instruct, free 10k neurons/day
    - On any non-retryable error: skip to next provider
    - Auto-switch: a provider that answers "429 too many requests" is skipped for
      COOLDOWN_MINUTES (remembered in agent_data.db across cloud runs), so the next
      free provider takes over automatically until the limit resets
    - Returns None if every provider fails or all keys are absent
"""
import datetime
import json
import os
import re
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.request

import guardrails

_TIMEOUT = 25  # seconds per request
_TIMEOUT_LARGE = 60  # long answers (product drafts) on the large model need more time


# Some APIs sit behind Cloudflare, which refuses Python's default "Python-urllib" user agent with 403.
_UA = {"User-Agent": "chilli-gavva/1.0"}


def _error_detail(exc) -> str:
    """A short public error code from the response body (e.g. "invalid_api_key", "1010"). Never the key."""
    import re
    try:
        raw = (exc.read() or b"")[:4000].decode("utf-8", "replace")
    except Exception:
        return ""
    if "error code: 1010" in raw or "error code:1010" in raw:
        return "cloudflare-1010"
    try:
        data = json.loads(raw)
        err = data[0] if isinstance(data, list) and data else data
        err = err.get("error", err) if isinstance(err, dict) else {}
        values = [err.get(k) for k in ("code", "status", "type")] if isinstance(err, dict) else []
        code = next((v for v in values if isinstance(v, str) and v), "")
    except Exception:
        return ""
    return code if re.fullmatch(r"[A-Za-z0-9_.\-]{1,40}", code) else ""


def _describe(exc) -> str:
    """Short, secret-free reason for a failed request: HTTP status (+ public error code), "timeout" or type."""
    if isinstance(exc, urllib.error.HTTPError):
        detail = _error_detail(exc)
        return f"{exc.code} {detail}".strip()
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        return "timeout" if isinstance(exc.reason, (socket.timeout, TimeoutError)) else "network"
    return type(exc).__name__


def _log_fail(provider: str, model: str, reason) -> None:
    """One short line per failed provider call so CI logs show WHY the AI failed. Never logs keys."""
    print(f"LLM {provider} {model}: FAIL {reason}", file=sys.stderr)

# ---------------------------------------------------------------------------
# Auto-switch: remember providers that hit their free limit (HTTP 429)
# ---------------------------------------------------------------------------
COOLDOWN_MINUTES = 30
_RATE_LIMITED: set = set()   # providers that returned 429 during the current call
_MEM_COOLDOWN: dict = {}      # fallback store when no database is available


def _note_429(name: str) -> None:
    _RATE_LIMITED.add(name)


def _cooldown_db(db_path=None):
    path = db_path or getattr(guardrails, "DB_PATH", None)
    if not path:
        return None
    try:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE IF NOT EXISTS llm_cooldown (provider TEXT PRIMARY KEY, until TEXT)")
        return conn
    except Exception:
        return None


def _now():
    return datetime.datetime.utcnow()


def set_cooldown(name: str, minutes: int = COOLDOWN_MINUTES, db_path=None) -> None:
    until = _now() + datetime.timedelta(minutes=minutes)
    _MEM_COOLDOWN[name] = until
    conn = _cooldown_db(db_path)
    if conn is None:
        return
    try:
        conn.execute("INSERT INTO llm_cooldown (provider, until) VALUES (?, ?) "
                     "ON CONFLICT(provider) DO UPDATE SET until = excluded.until", (name, until.isoformat()))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def in_cooldown(name: str, db_path=None) -> bool:
    until = _MEM_COOLDOWN.get(name)
    conn = _cooldown_db(db_path)
    if conn is not None:
        try:
            row = conn.execute("SELECT until FROM llm_cooldown WHERE provider = ?", (name,)).fetchone()
            if row:
                stored = datetime.datetime.fromisoformat(row[0])
                until = max(until, stored) if until else stored
        except Exception:
            pass
        finally:
            conn.close()
    return bool(until and until > _now())


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
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **_UA}
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
# Model sizes: "small" = fast/cheap model for simple jobs (research notes, checks),
# "large" = stronger model for customer-facing drafts. Override with env vars.
SMALL = "small"
LARGE = "large"
_LARGE_PROMPT_CHARS = 3000  # auto mode: prompts longer than this go to the large model


def _groq_models(size: str) -> list[str]:
    small = os.environ.get("GROQ_MODEL_SMALL", "openai/gpt-oss-20b")
    large = os.environ.get("GROQ_MODEL_LARGE", "openai/gpt-oss-120b")
    # Large falls back to small (e.g. when the large model's daily free limit is used up)
    return [large, small] if size == LARGE else [small]


def _groq(prompt: str, size: str = SMALL) -> str | None:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key or api_key.startswith("YOUR_"):
        return None
    timeout = _TIMEOUT_LARGE if size == LARGE else _TIMEOUT
    for model in _groq_models(size):
        result = _groq_call(prompt, api_key, model, timeout=timeout)
        if result:
            return result
    return None


def _groq_call(prompt: str, api_key: str, model: str, timeout: float = _TIMEOUT) -> str | None:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **_UA}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }

    for attempt in (1, 2):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(), headers=headers
            )
            guardrails.record_call()
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                text = data["choices"][0]["message"]["content"].strip() or None
                if not text:
                    _log_fail("Groq", model, "empty answer")
                return text
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 2:
                _note_429("Groq")
            if e.code == 429 and attempt == 1:
                wait = 5.0
                try:
                    wait = min(float(e.headers.get("retry-after", "5")), 20.0)
                except (ValueError, TypeError):
                    pass
                time.sleep(wait)
                continue
            _log_fail("Groq", model, _describe(e))
            return None
        except Exception as exc:
            _log_fail("Groq", model, _describe(exc))
            return None
    return None


# ---------------------------------------------------------------------------
# Provider 2: Google Gemini (free tier)
# ---------------------------------------------------------------------------
def _openai_style_call(url: str, api_key: str, model: str, prompt: str, name: str, extra_headers=None,
                       timeout: float = _TIMEOUT):
    """POST an OpenAI-compatible chat request. Returns (text_or_None, http_status_or_None)."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **_UA}
    if extra_headers:
        headers.update(extra_headers)
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7}
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        guardrails.record_call()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if not text:
                _log_fail(name, model, "empty answer")
            return (text or None), 200
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _note_429(name)
        _log_fail(name, model, _describe(e))
        return None, e.code
    except Exception as exc:
        _log_fail(name, model, _describe(exc))
        return None, None


def _gemini_models() -> list[str]:
    raw = os.environ.get("GEMINI_MODEL", "") or "gemini-3.8-flash,gemini-flash-latest,gemini-3.5-flash"
    return [m.strip() for m in raw.split(",") if m.strip()]


def _gemini(prompt: str, timeout: float = _TIMEOUT) -> str | None:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or api_key.startswith("YOUR_"):
        return None
    url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    for model in _gemini_models():
        text, status = _openai_style_call(url, api_key, model, prompt, "Gemini", timeout=timeout)
        if text:
            return text
        if status in (400, 404, 500, 503) or status is None:
            continue  # model retired, busy (503) or timed out: try the next one
        return None   # rate limit or auth problem: stop here
    return None


# ---------------------------------------------------------------------------
# Provider 3: OpenRouter (free models, OpenAI-compatible)
# ---------------------------------------------------------------------------
def _openrouter(prompt: str) -> str | None:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    model = os.environ.get("OPENROUTER_MODEL", "").strip()
    if not api_key or api_key.startswith("YOUR_") or not model:
        return None
    url = "https://openrouter.ai/api/v1/chat/completions"
    text, _ = _openai_style_call(url, api_key, model, prompt, "OpenRouter",
                                 extra_headers={"X-Title": "Chilli Gavva"})
    return text


# ---------------------------------------------------------------------------
# Provider 4: Cloudflare Workers AI (free 10k neurons/day)
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
        **_UA,
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
    except Exception as exc:
        _log_fail("Cloudflare", "llama-3.1-8b-instruct", _describe(exc))
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
_PROVIDERS = [
    ("FreeLLMAPI", _freellmapi),
    ("Groq", _groq),
    ("Gemini", _gemini),
    ("OpenRouter", _openrouter),
    ("Cloudflare", _cloudflare),
]


def pick_size(prompt: str, size: str | None = None) -> str:
    """Resolve the model size: explicit "small"/"large" wins, otherwise decide by prompt length."""
    if size in (SMALL, LARGE):
        return size
    return LARGE if len(prompt or "") > _LARGE_PROMPT_CHARS else SMALL


def chat(prompt: str, *, db_path=None, size: str | None = None) -> str | None:
    """Try each provider in order; return first non-None result.

    size: "small" (fast model), "large" (stronger model) or None (auto by prompt length).
    Only Groq has both sizes today; other providers use their single free model.
    Respects guardrails.can_call_llm() before the first attempt.
    Returns None if all providers fail or daily cap is hit.
    """
    if not guardrails.can_call_llm(db_path):
        print("LLM: skipped (kill switch on or daily call cap reached)", file=sys.stderr)
        return None

    resolved = pick_size(prompt, size)
    _mod_globals = globals()
    order = [("FreeLLMAPI", "_freellmapi"), ("Groq", "_groq"), ("Gemini", "_gemini"),
             ("OpenRouter", "_openrouter"), ("Cloudflare", "_cloudflare")]
    for name, fn_name in order:
        if in_cooldown(name, db_path):
            continue  # this free provider hit its limit recently: let the next one work
        fn = _mod_globals[fn_name]
        _RATE_LIMITED.discard(name)
        try:
            if name == "Groq":
                result = fn(prompt, size=resolved)
            elif name == "Gemini" and resolved == LARGE:
                result = fn(prompt, timeout=_TIMEOUT_LARGE)  # long drafts need more than 25 s
            else:
                result = fn(prompt)
        except Exception:
            result = None
        if result:
            return result
        if name in _RATE_LIMITED:
            set_cooldown(name, db_path=db_path)

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
        elif name == "OpenRouter":
            key = os.environ.get("OPENROUTER_API_KEY", "")
            configured = bool(key and not key.startswith("YOUR_") and os.environ.get("OPENROUTER_MODEL", "").strip())
        elif name == "Cloudflare":
            tok = os.environ.get("CF_API_TOKEN", "")
            acc = os.environ.get("CF_ACCOUNT_ID", "")
            configured = bool(tok and acc and not tok.startswith("YOUR_"))
        else:
            configured = False
        status.append({"provider": name, "configured": configured})
    return status


# ---------------------------------------------------------------------------
# Health check: python3 llm_router.py health
# ---------------------------------------------------------------------------
HEALTH_PROMPT = "Reply with the single word OK."
LONG_PROMPT = ("Write a complete product spec and store listing (about 600 words) for a Google Sheets budget "
               "template for freelancers with irregular income. Use these labelled sections: PRODUCT NAME, BUYER, "
               "PRICE (in USD), PLATFORM, WHAT'S INSIDE (every tab with its columns), HOW TO USE, LISTING TITLE, "
               "LISTING DESCRIPTION, SEARCH TAGS (exactly 5). No income promises.")


def _probe(url, headers, body, timeout, parse):
    """One request for the health check. Returns (ok, reason, seconds). reason never contains secrets."""
    started = time.time()
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = parse(json.loads(resp.read().decode()))
        ok = bool(text and text.strip())
        return ok, ("" if ok else "empty answer"), time.time() - started
    except Exception as exc:
        return False, _describe(exc), time.time() - started


def _chat_parse(data):
    return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def _targets():
    """(provider, model, url, headers, extra_body, parse) for every configured provider and model."""
    out = []
    key = os.environ.get("GROQ_API_KEY", "")
    if key and not key.startswith("YOUR_"):
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **_UA}
        for model in dict.fromkeys(_groq_models(LARGE)[::-1]):  # small first, then large
            out.append(("Groq", model, "https://api.groq.com/openai/v1/chat/completions", headers, _chat_parse))
    key = os.environ.get("GEMINI_API_KEY", "")
    if key and not key.startswith("YOUR_"):
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **_UA}
        for model in _gemini_models():
            out.append(("Gemini", model, "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                        headers, _chat_parse))
    key, model = os.environ.get("OPENROUTER_API_KEY", ""), os.environ.get("OPENROUTER_MODEL", "").strip()
    if key and model and not key.startswith("YOUR_"):
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "Chilli Gavva",
                   **_UA}
        out.append(("OpenRouter", model, "https://openrouter.ai/api/v1/chat/completions", headers, _chat_parse))
    tok, acc = os.environ.get("CF_API_TOKEN", ""), os.environ.get("CF_ACCOUNT_ID", "")
    if tok and acc and not tok.startswith("YOUR_"):
        headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", **_UA}
        url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/@cf/meta/llama-3.1-8b-instruct"
        out.append(("Cloudflare", "llama-3.1-8b-instruct", url, headers,
                    lambda d: (d.get("result") or {}).get("response") or ""))
    return out


def health(probe=_probe, long_timeout=90):
    """Run one tiny test per provider/model, then one long-answer test on the first large model that works.

    Returns a list of result dicts. Nothing here contains a key: only provider, model, ok, reason, seconds.
    """
    results = []
    targets = _targets()
    for provider, model, url, headers, parse in targets:
        body = {"model": model, "messages": [{"role": "user", "content": HEALTH_PROMPT}]}
        ok, reason, secs = probe(url, headers, body, _TIMEOUT, parse)
        results.append({"test": "tiny", "provider": provider, "model": model, "ok": ok,
                        "reason": reason, "seconds": round(secs, 1)})
    large = os.environ.get("GROQ_MODEL_LARGE", "openai/gpt-oss-120b")
    working = [r for r in results if r["ok"]]
    # Same order as real drafts: the Groq large model first, then every other model that answered.
    working.sort(key=lambda r: not (r["provider"] == "Groq" and r["model"] == large))
    for pick in working:
        target = next(t for t in targets if t[0] == pick["provider"] and t[1] == pick["model"])
        body = {"model": target[1], "messages": [{"role": "user", "content": LONG_PROMPT}]}
        ok, reason, secs = probe(target[2], target[3], body, long_timeout, target[4])
        if ok and secs > _TIMEOUT:
            reason = f"slower than the old {_TIMEOUT}s limit"
        results.append({"test": "long", "provider": target[0], "model": target[1], "ok": ok,
                        "reason": reason, "seconds": round(secs, 1)})
        if ok:
            break
    return results


def gemini_model_names(opener=None) -> list[str]:
    """Names of the Gemini chat models this key can use (public model ids only), newest-looking first."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key or key.startswith("YOUR_"):
        return []
    req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/openai/models",
                                 headers={"Authorization": f"Bearer {key}", **_UA})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        return [f"(could not list models: {_describe(exc)})"]
    names = [str(m.get("id", "")).replace("models/", "") for m in data.get("data", []) if isinstance(m, dict)]
    names = [n for n in names if n.startswith("gemini") and ("flash" in n or "pro" in n)
             and not any(x in n for x in ("tts", "image", "audio", "live", "embedding"))]
    return sorted(set(names), reverse=True)[:12]


def groq_model_names(opener=None) -> list[str]:
    """Names of the Groq chat models this key can use (public model ids only)."""
    key = os.environ.get("GROQ_API_KEY", "")
    if not key or key.startswith("YOUR_"):
        return []
    req = urllib.request.Request("https://api.groq.com/openai/v1/models",
                                 headers={"Authorization": f"Bearer {key}", **_UA})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        return [f"(could not list models: {_describe(exc)})"]
    names = [str(m.get("id", "")) for m in data.get("data", []) if isinstance(m, dict)]
    names = [n for n in names if re.fullmatch(r"[A-Za-z0-9_./:\-]{1,80}", n)
             and not any(x in n.lower() for x in ("whisper", "tts", "guard", "playai", "orpheus", "distil"))]
    return sorted(set(names))[:20]


def format_health(results):
    if not results:
        return "No AI provider is configured (no API keys found)."
    lines = [f"{'TEST':5} {'PROVIDER':11} {'MODEL':32} RESULT"]
    for r in results:
        verdict = "OK" if r["ok"] else "FAIL"
        extra = f" {r['reason']}" if r["reason"] else ""
        lines.append(f"{r['test']:5} {r['provider']:11} {r['model'][:32]:32} {verdict}{extra} ({r['seconds']}s)")
    return "\n".join(lines)


if __name__ == "__main__":
    if sys.argv[1:2] == ["health"]:
        print(format_health(health()))
        models = gemini_model_names()
        if models:
            print("Gemini models this key can use: " + ", ".join(models))
        models = groq_model_names()
        if models:
            print("Groq models this key can use: " + ", ".join(models))
    else:
        print("usage: python3 llm_router.py health")
