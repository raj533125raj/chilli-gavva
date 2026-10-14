"""LLM fallback router: FreeLLMAPI (local) → Groq → Gemini (free) → OpenRouter (free) → Cloudflare Workers AI.

All of them use OpenAI-compatible /v1/chat/completions, all via stdlib urllib — zero third-party deps.

Usage:
    import llm_router
    text = llm_router.chat("write a headline about Indian spices")          # auto size
    text = llm_router.chat(long_prompt, size="large")                         # strong model
    # Returns str on success, None if all providers failed.

Environment variables (all optional — missing key = skip that provider):
    FREELLMAPI_KEY       freellmapi local router token (starts freellmapi-). Run locally: freellmapi.co
    FREELLMAPI_BASE_URL  Override local router URL (default: http://localhost:3001/v1)
    GROQ_API_KEY         Groq console.groq.com — free, no card
    GEMINI_API_KEY       aistudio.google.com/apikey — free, no card
    GEMINI_MODEL         Optional comma list of Gemini models to try (default: gemini-3.8-flash,gemini-2.5-flash)
    OPENROUTER_API_KEY   openrouter.ai free account key
    OPENROUTER_MODEL     Required with OPENROUTER_API_KEY: a current free model id (ends with ":free")
    CF_API_TOKEN         Cloudflare API token with Workers AI read
    CF_ACCOUNT_ID        Cloudflare account ID (Settings → scroll down)

Behaviour:
    - FreeLLMAPI: pools 7B+ free tokens/month from 34 providers — used first when running locally
    - Groq: size routing — "small" = llama-3.1-8b-instant, "large" = llama-3.3-70b-versatile
      (large falls back to small), retries once on 429
    - Gemini: OpenAI-compatible endpoint, tries GEMINI_MODEL list in order (unknown model -> next)
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
import sqlite3
import time
import urllib.error
import urllib.request

import guardrails

_TIMEOUT = 25  # seconds per request

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
# Model sizes: "small" = fast/cheap model for simple jobs (research notes, checks),
# "large" = stronger model for customer-facing drafts. Override with env vars.
SMALL = "small"
LARGE = "large"
_LARGE_PROMPT_CHARS = 3000  # auto mode: prompts longer than this go to the large model


def _groq_models(size: str) -> list[str]:
    small = os.environ.get("GROQ_MODEL_SMALL", "llama-3.1-8b-instant")
    large = os.environ.get("GROQ_MODEL_LARGE", "llama-3.3-70b-versatile")
    # Large falls back to small (e.g. when the large model's daily free limit is used up)
    return [large, small] if size == LARGE else [small]


def _groq(prompt: str, size: str = SMALL) -> str | None:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key or api_key.startswith("YOUR_"):
        return None
    for model in _groq_models(size):
        result = _groq_call(prompt, api_key, model)
        if result:
            return result
    return None


def _groq_call(prompt: str, api_key: str, model: str) -> str | None:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
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
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
                return data["choices"][0]["message"]["content"].strip() or None
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
            return None
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Provider 2: Google Gemini (free tier)
# ---------------------------------------------------------------------------
def _openai_style_call(url: str, api_key: str, model: str, prompt: str, name: str, extra_headers=None):
    """POST an OpenAI-compatible chat request. Returns (text_or_None, http_status_or_None)."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7}
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        guardrails.record_call()
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            return (text or None), 200
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _note_429(name)
        return None, e.code
    except Exception:
        return None, None


def _gemini_models() -> list[str]:
    raw = os.environ.get("GEMINI_MODEL", "") or "gemini-3.8-flash,gemini-2.5-flash"
    return [m.strip() for m in raw.split(",") if m.strip()]


def _gemini(prompt: str) -> str | None:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or api_key.startswith("YOUR_"):
        return None
    url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    for model in _gemini_models():
        text, status = _openai_style_call(url, api_key, model, prompt, "Gemini")
        if text:
            return text
        if status in (400, 404):
            continue  # model name not available any more: try the next one
        return None   # rate limit, auth problem or network error: stop here
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
            result = fn(prompt, size=resolved) if name == "Groq" else fn(prompt)
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
