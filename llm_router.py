"""LLM fallback router using stdlib HTTP transports and public-safe health records.

Default order: Groq, Gemini, Cloudflare Workers AI, OpenRouter, FreeLLMAPI.
Recent success rates prefer reliable providers per task (small/large by default;
chat(task_kind="research"/"draft"/"review"/"code") supplies a bounded task label).
Ties preserve the default order. Unknown tasks become "general", never prompt text.

Optional credentials: GROQ_API_KEY, GEMINI_API_KEY, GEMINI_API_KEY_2,
OPENROUTER_API_KEY plus OPENROUTER_MODEL, CF_API_TOKEN plus CF_ACCOUNT_ID,
FREELLMAPI_KEY (local). Missing credentials skip that provider. The separate
auto-fix credential is never selected for router requests.

Cloudflare uses the native /ai/run endpoint with the current FP8 Llama model;
CF_MODEL optionally supplies comma-separated native model ids. Large requests
use a longer timeout/output budget; oversized requests skip without truncation.

Each provider/model has a persistent circuit breaker (three consecutive failures
of the same kind; five minute cooldown, then one half-open probe) and UTC daily
attempt cap. LLM_MODEL_DAILY_CAPS overrides individual caps with nested JSON:
{"Cloudflare": {"@cf/meta/llama-3.1-8b-instruct-fp8": 20}}. Defaults are 40 for
Cloudflare and 100 for other models; these are local safety caps, not vendor quota
estimates. Every retry, key rotation and media fallback shares the reservations.
Account rate limits retain the 30 minute cooldown; Cloudflare daily quota waits
until the next UTC day instead of repeatedly retrying an exhausted allocation.

provider_status() includes model caps, circuits and fixed failure reasons;
get_provider_health() returns public-safe observations without making requests.
python3 llm_router.py health probes use the same model caps and breakers as chat.
No provider error messages, response bodies, credentials or prompts are stored
in health. All runtime calls still obey the global budget and injection guard.
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
import contextvars
import functools
from contextlib import nullcontext

import guardrails
import injection_guard
import secret_guard
import llm_provider_state as provider_state

# --- Prompt-injection guard flag (OFF by default) --------------------------
GUARD_ENABLED = os.environ.get("CHILLI_GUARD_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
BLOCKED_BY_GUARD = "blocked_by_guard"


def _high_guard_reasons(text):
    """High-severity injection pattern names found in ``text`` (never the match text)."""
    if not text:
        return []
    try:
        findings = injection_guard.scan_text(text)
    except Exception:
        return []
    reasons = []
    for finding in findings:
        if finding.severity == "high" and finding.pattern not in reasons:
            reasons.append(finding.pattern)
    return reasons


def blocked_by_guard_result(reasons):
    """A clear, secret-free result string for a prompt blocked by the guard."""
    return (f"{BLOCKED_BY_GUARD}: high-severity prompt-injection pattern(s) refused: " + ", ".join(reasons))


_TIMEOUT = 25  # seconds per request
_TIMEOUT_LARGE = 60  # long answers (product drafts) on the large model need more time


# Some APIs sit behind Cloudflare, which refuses Python's default "Python-urllib" user agent with 403.
_UA = {"User-Agent": "chilli-gavva/1.0"}

# These are local safety caps, not estimates of vendors' token/neurons quotas.
# Override individual models with LLM_MODEL_DAILY_CAPS JSON, for example:
# {"Cloudflare": {"@cf/meta/llama-3.1-8b-instruct-fp8": 20}}.
DEFAULT_MODEL_DAILY_CAP = 100
_TASK_KINDS = {"general", "small", "large", "research", "draft", "review", "code", "vision", "audio", "health"}
_ROUTER_DB = contextvars.ContextVar("llm_router_db", default=None)
_TASK = contextvars.ContextVar("llm_router_task", default="general")
_RETRY_AFTER = contextvars.ContextVar("llm_retry_after", default=5.0)
_ATTEMPTS = contextvars.ContextVar("llm_provider_attempts", default=0)
_LAST_FAILURE_KIND = contextvars.ContextVar("llm_last_failure_kind", default="")
_MEMORY_STATE = provider_state._memory


def _state_path(db_path=None):
    path = db_path or _ROUTER_DB.get()
    budget = getattr(guardrails, "_BUDGET_DB", None)
    budget_path = budget.get() if budget is not None else None
    testing = provider_state.running_tests() or getattr(guardrails, "_TESTING", False)
    # A budget decorator's implicit DB_PATH is not an explicit fixture database.
    if not path and testing and budget_path == getattr(guardrails, "DB_PATH", None):
        return _MEMORY_STATE
    path = path or budget_path
    if path == ":memory:":
        return _MEMORY_STATE
    if path:
        return provider_state.safe_path(path)
    if testing:
        return _MEMORY_STATE
    return getattr(guardrails, "DB_PATH", None) or _MEMORY_STATE


def _budget_context(path):
    context = getattr(guardrails, "budget_context", None)
    path = ":memory:" if isinstance(path, sqlite3.Connection) else path
    return context(path) if context else nullcontext()


def _router_scoped(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        path = _state_path(kwargs.get("db_path"))
        if kwargs.get("db_path") is not None and isinstance(path, sqlite3.Connection):
            kwargs = dict(kwargs, db_path=":memory:")
        db_token = _ROUTER_DB.set(path)
        task = kwargs.get("task_kind") or {
            "chat_vision": "vision", "chat_audio": "audio", "health": "health"
        }.get(function.__name__)
        if task is None:
            task = pick_size(args[0] if args else kwargs.get("prompt", ""), kwargs.get("size"))
        task_token = _TASK.set(task if task in _TASK_KINDS else "general")
        attempts_token = _ATTEMPTS.set(0)
        try:
            with _budget_context(path):
                return function(*args, **kwargs)
        finally:
            _TASK.reset(task_token)
            _ATTEMPTS.reset(attempts_token)
            _ROUTER_DB.reset(db_token)
    return wrapped


def _model_cap(provider, model):
    default = 40 if provider == "Cloudflare" else DEFAULT_MODEL_DAILY_CAP
    try:
        caps = json.loads(os.environ.get("LLM_MODEL_DAILY_CAPS", "{}"))
        cap = caps.get(provider, {}).get(model, default)
        return min(500, max(0, cap)) if type(cap) is int else 0
    except (ValueError, TypeError, AttributeError):
        return 0  # malformed cap configuration must not silently remove a limit


def _public_model(model):
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9@_./:\-]{1,100}", model):
        return "configured-model"
    return model if scrub(model) == model else "configured-model"


class _ProviderError(ValueError):
    def __init__(self, kind, status=None):
        self.kind = kind if kind in provider_state.REASONS else "provider_error"
        self.status = status
        super().__init__(self.kind)


def _failure_kind(exc, description=""):
    if isinstance(exc, _ProviderError):
        return exc.kind
    if description in ("quota", "rate limit", "timeout", "network"):
        return {"rate limit": "rate_limit"}.get(description, description)
    code = description.split()[-1] if description else ""
    if code in ("3036",):
        return "quota"
    if code in ("3040", "4006"):
        return "capacity"
    if code in ("5007", "3042", "model_not_found", "NOT_FOUND") or getattr(exc, "code", None) == 404:
        return "invalid_model"
    if code == "3006" or getattr(exc, "code", None) == 413:
        return "request_too_large"
    if code in ("3007", "3008") or getattr(exc, "code", None) in (408, 504):
        return "timeout"
    if getattr(exc, "code", None) in (401, 403):
        return "authentication"
    if isinstance(exc, (ValueError, KeyError, IndexError, TypeError, AttributeError)):
        return "response_parse"
    if isinstance(exc, OSError) and not isinstance(exc, urllib.error.HTTPError):
        return "network"
    return "provider_error"


def _request_json(req, provider, model, timeout, parse):
    """All runtime HTTP attempts share reservations, breakers and safe diagnostics."""
    _LAST_FAILURE_KIND.set("")
    if provider == "Cloudflare" and in_cooldown(provider, _ROUTER_DB.get()):
        return None, None
    if _public_model(model) != model or not provider_state.reserve(
            provider, model, _model_cap(provider, model), _state_path()):
        return None, None
    try:
        with _budget_context(_state_path()):
            guardrails.record_call()
    except Exception:
        # A global budget rejection is not a provider failure.
        return None, None
    try:
        _ATTEMPTS.set(_ATTEMPTS.get() + 1)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = parse(json.loads(resp.read().decode()))
        if not isinstance(text, str):
            raise _ProviderError("response_parse")
        text = text.strip()
        if not text:
            raise _ProviderError("empty_response")
    except Exception as exc:
        if getattr(exc, "code", None) == 429:
            try:
                _RETRY_AFTER.set(max(0.0, min(float(exc.headers.get("retry-after", "5")), 20.0)))
            except (ValueError, TypeError, AttributeError):
                _RETRY_AFTER.set(5.0)
        description = _describe(exc)
        kind = _failure_kind(exc, description)
        _LAST_FAILURE_KIND.set(kind)
        provider_state.observe(provider, model, False, kind, _TASK.get(), _state_path())
        if provider == "Cloudflare" and kind == "quota":
            now = _now()
            midnight = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            set_cooldown(provider, minutes=(midnight - now).total_seconds() / 60,
                         db_path=_ROUTER_DB.get())
        _log_fail(provider, model, description)
        return None, getattr(exc, "code", getattr(exc, "status", None))
    provider_state.observe(provider, model, True, task=_TASK.get(), path=_state_path())
    return text, 200


def _error_detail(exc) -> str:
    """A short public error code from the response body (e.g. "invalid_api_key", "1010"). Never the key."""
    import re
    try:
        raw = (exc.read(4000) or b"").decode("utf-8", "replace")
    except Exception:
        return ""
    if "error code: 1010" in raw or "error code:1010" in raw:
        return "cloudflare-1010"
    try:
        data = json.loads(raw)
        err = data[0] if isinstance(data, list) and data else data
        if isinstance(err, dict) and err.get("errors"):
            err = err["errors"][0]
        err = err.get("error", err) if isinstance(err, dict) else {}
        values = [err.get(k) for k in ("code", "status", "type")] if isinstance(err, dict) else []
        known = {"invalid_api_key", "invalid_request_error", "NOT_FOUND", "UNAVAILABLE", "RESOURCE_EXHAUSTED",
                 "PERMISSION_DENIED", "UNAUTHENTICATED", "INVALID_ARGUMENT", "model_not_found",
                 "insufficient_quota", "quota_exceeded", "daily_limit_exceeded", "rate_limit_exceeded",
                 "3006", "3007", "3008", "3036", "3040", "3042", "4006", "5007", "5035"}
        code = next((str(v) for v in values if str(v) in known), "")
    except Exception:
        return ""
    return code if re.fullmatch(r"[A-Za-z0-9_.\-]{1,40}", code) else ""


def _describe(exc) -> str:
    """Short, secret-free reason for a failed request: quota, rate limit, down, timeout or HTTP status."""
    if isinstance(exc, _ProviderError):
        return "empty answer" if exc.kind == "empty_response" else exc.kind
    if isinstance(exc, urllib.error.HTTPError):
        detail = _error_detail(exc)
        detail_lower = detail.lower()
        if exc.code == 429:
            if "quota" in detail_lower or "daily" in detail_lower or detail == "3036":
                return "quota"
            if detail in ("3040", "4006"):
                return f"429 {detail}"
            return "rate limit"
        return f"{exc.code} {detail}".strip()
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        return "timeout" if isinstance(exc.reason, (socket.timeout, TimeoutError)) else "network"
    return type(exc).__name__ if type(exc).__name__ in {
        "OSError", "ValueError", "KeyError", "IndexError", "TypeError", "AttributeError", "JSONDecodeError"
    } else "provider error"


def _log_fail(provider: str, model: str, reason) -> None:
    """One short line per failed provider call so CI logs show WHY the AI failed. Never logs keys."""
    print(scrub(f"LLM {provider} {_public_model(model)}: FAIL {reason}"), file=sys.stderr)

# ---------------------------------------------------------------------------
# Auto-switch: remember providers that hit their free limit (HTTP 429)
# ---------------------------------------------------------------------------
COOLDOWN_MINUTES = 30
_RATE_LIMITED: set = set()   # providers that returned 429 during the current call
_MEM_COOLDOWN: dict = {}      # fallback store when no database is available


def _note_429(name: str) -> None:
    _RATE_LIMITED.add(name)


def _cooldown_db(db_path=None):
    path = _state_path(db_path)
    if isinstance(path, sqlite3.Connection):
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
    key = (_state_path(db_path), name)
    if minutes >= 0:
        until = max(until, _MEM_COOLDOWN.get(key, until))
    _MEM_COOLDOWN[key] = until
    conn = _cooldown_db(db_path)
    if conn is None:
        return
    try:
        if minutes >= 0:
            row = conn.execute("SELECT until FROM llm_cooldown WHERE provider=?", (name,)).fetchone()
            if row:
                until = max(until, datetime.datetime.fromisoformat(row[0]))
        conn.execute("INSERT INTO llm_cooldown (provider, until) VALUES (?, ?) "
                     "ON CONFLICT(provider) DO UPDATE SET until = excluded.until", (name, until.isoformat()))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def in_cooldown(name: str, db_path=None) -> bool:
    until = _MEM_COOLDOWN.get((_state_path(db_path), name))
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
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    text, _ = _request_json(req, "FreeLLMAPI", "auto", _TIMEOUT, _chat_parse)
    return text


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
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        text, status = _request_json(req, "Groq", model, timeout, _chat_parse)
        if text:
            return text
        if status != 429:
            return None
        if _LAST_FAILURE_KIND.get() == "quota":
            _note_429("Groq")
            return None
        if attempt == 1:
            time.sleep(_RETRY_AFTER.get())
        else:
            _note_429("Groq")
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
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    text, status = _request_json(req, name, model, timeout, _chat_parse)
    if status == 429:
        _note_429(name)
    return text, status


def _gemini_models() -> list[str]:
    raw = os.environ.get("GEMINI_MODEL", "") or "gemini-3.8-flash,gemini-flash-latest,gemini-3.5-flash"
    return [m.strip() for m in raw.split(",") if m.strip()]


def _gemini(prompt: str, timeout: float = _TIMEOUT) -> str | None:
    primary_key = os.environ.get("GEMINI_API_KEY", "")
    secondary_key = os.environ.get("GEMINI_API_KEY_2", "")
    keys_to_try = [k for k in [primary_key, secondary_key] if k and not k.startswith("YOUR_")]
    if not keys_to_try:
        return None
    url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    for api_key in keys_to_try:
        statuses = []
        for model in _gemini_models():
            text, status = _openai_style_call(url, api_key, model, prompt, "Gemini", timeout=timeout)
            if text:
                _RATE_LIMITED.discard("Gemini")
                return text
            statuses.append(status)
            if status in (400, 404, 429, 500, 503) or status is None:
                continue  # retired, over limit (429), busy or timed out: try next model
            break  # auth problem: try next key if available
        if any(s != 429 for s in statuses):
            _RATE_LIMITED.discard("Gemini")
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
# The original unquantized model has left the current catalogue. Use the
# documented FP8 model (32k context), not an invented/retired model id.
# https://developers.cloudflare.com/workers-ai/models/llama-3.1-8b-instruct-fp8/
_CF_MODEL = "@cf/meta/llama-3.1-8b-instruct-fp8"
_CF_CONTEXT_TOKENS = 32000


def _cloudflare_models():
    raw = os.environ.get("CF_MODEL", "") or _CF_MODEL
    # Explicit overrides allow rollout without another code change. Unsafe ids
    # are ignored and never echoed to health or used in a request URL.
    return list(dict.fromkeys(m.strip() for m in raw.split(",")
                             if m.strip().startswith("@cf/") and _public_model(m.strip()) == m.strip()))


def _cf_parse(data):
    if not isinstance(data, dict):
        raise _ProviderError("response_parse")
    if data.get("success") is False or data.get("errors"):
        errors = data.get("errors") or [{}]
        code = str(errors[0].get("code", "")) if isinstance(errors, list) and isinstance(errors[0], dict) else ""
        # Workers AI returns errors[].code as an integer, including inside HTTP
        # 200 envelopes. Never retain message: it can echo the request or a key.
        kinds = {"5007": ("invalid_model", 400), "3042": ("invalid_model", 404),
                 "3006": ("request_too_large", 413), "3007": ("timeout", 408),
                 "3008": ("timeout", 408), "3036": ("quota", 429),
                 "3040": ("capacity", 429), "4006": ("capacity", 429),
                 "5035": ("authentication", 403)}
        raise _ProviderError(*kinds.get(code, ("provider_error", None)))
    result = data.get("result", data)
    if not isinstance(result, dict):
        raise _ProviderError("response_parse")
    return result.get("response", "")


def _cloudflare(prompt: str, size: str | None = None) -> str | None:
    token = os.environ.get("CF_API_TOKEN", "")
    account_id = os.environ.get("CF_ACCOUNT_ID", "")
    if not token or not account_id or token.startswith("YOUR_"):
        return None
    resolved = pick_size(prompt, size)
    timeout = _TIMEOUT_LARGE if resolved == LARGE else _TIMEOUT
    output_tokens = 4096 if resolved == LARGE else 1024
    # UTF-8 bytes bound the input token count conservatively (including non-ASCII
    # text); leave room for the chat template. Skip oversized input intact rather
    # than sending a request guaranteed to exceed this model's context window.
    if len(prompt.encode("utf-8")) + output_tokens + 512 > _CF_CONTEXT_TOKENS:
        return None
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": output_tokens}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", **_UA}
    for model in _cloudflare_models():
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        text, status = _request_json(req, "Cloudflare", model, timeout, _cf_parse)
        if text:
            return text
        # Model circuits/caps handle repeated failures independently. Account
        # quota/rate errors also preserve the existing provider-level cooldown.
        if status == 429 and _LAST_FAILURE_KIND.get() in ("quota", "rate_limit"):
            _note_429("Cloudflare")
            break  # an account-wide limit cannot be solved by another model
    return None


# ---------------------------------------------------------------------------
# Provider: GitHub Models (preview; uses the workflow's built-in GITHUB_TOKEN)
# Off unless the repo variable GITHUB_MODELS is "on" (turn it on only after the health check says OK).
# ---------------------------------------------------------------------------
_GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"


def _github_models_token() -> str:
    return os.environ.get("GITHUB_MODELS_TOKEN", "").strip()


def _github_models_list() -> list[str]:
    raw = os.environ.get("GITHUB_MODELS_MODEL", "") or "openai/gpt-4.1-mini,openai/gpt-4o-mini"
    return [m.strip() for m in raw.split(",") if m.strip()]


def github_models_enabled() -> bool:
    return bool(_github_models_token()) and os.environ.get("GITHUB_MODELS", "").strip().lower() == "on"


def _github_models(prompt: str, timeout: float = _TIMEOUT) -> str | None:
    if not github_models_enabled():
        return None
    for model in _github_models_list():
        text, status = _openai_style_call(_GITHUB_MODELS_URL, _github_models_token(), model, prompt,
                                          "GitHubModels", timeout=timeout)
        if text:
            return text
        if status in (400, 404, 500, 503) or status is None:
            continue
        return None
    return None


# ---------------------------------------------------------------------------
# Privacy: never send private data (keys, tokens, Telegram chat id) inside a prompt
# ---------------------------------------------------------------------------
def scrub(prompt: str) -> str:
    """Use the shared scrubber for prompts, diagnostics and stored provider text."""
    return secret_guard.scrub_text(prompt)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
# Order: Groq first, then Gemini, GitHub Models, Cloudflare, OpenRouter. FreeLLMAPI (local only) is last.
_PROVIDERS = [
    ("Groq", _groq),
    ("Gemini", _gemini),
    ("Cloudflare", _cloudflare),
    ("OpenRouter", _openrouter),
    ("FreeLLMAPI", _freellmapi),
]


def pick_size(prompt: str, size: str | None = None) -> str:
    """Resolve the model size: explicit "small"/"large" wins, otherwise decide by prompt length."""
    if size in (SMALL, LARGE):
        return size
    return LARGE if len(prompt or "") > _LARGE_PROMPT_CHARS else SMALL


@_router_scoped
@guardrails.budget_scoped
def chat(prompt: str, *, db_path=None, size: str | None = None, task_kind: str | None = None) -> str | None:
    """Try each provider in order; return first non-None result.

    size: "small" (fast model), "large" (stronger model) or None (auto by prompt length).
    Only Groq has both sizes today; other providers use their single free model.
    Respects guardrails.can_call_llm() before the first attempt.
    Returns None if all providers fail or daily cap is hit.
    task_kind: optional bounded label (research/draft/review/code); defaults to
    small/large. Recent success rates reorder providers within that task only.
    """
    if not guardrails.can_call_llm(db_path):
        print("LLM: skipped (kill switch on or daily call cap reached)", file=sys.stderr)
        return None

    prompt = scrub(prompt)
    if GUARD_ENABLED:
        guard_reasons = _high_guard_reasons(prompt)
        if guard_reasons:
            # Defense in depth: do not dispatch an untrusted instruction to any provider.
            # The reason lists pattern names only (no secret text).
            return blocked_by_guard_result(guard_reasons)
    resolved = pick_size(prompt, size)
    _mod_globals = globals()
    order = provider_state.order([(name, fn.__name__) for name, fn in _PROVIDERS],
                                 _TASK.get(), _state_path(db_path))
    try:
        configured = {s["provider"] for s in provider_status() if s["configured"]}
    except Exception:
        configured = set()
    for name, fn_name in order:
        if in_cooldown(name, db_path):
            continue  # this free provider hit its limit recently: let the next one work
        fn = _mod_globals[fn_name]
        _RATE_LIMITED.discard(name)
        before_attempts = _ATTEMPTS.get()
        try:
            if name in ("Groq", "Cloudflare"):
                result = fn(prompt, size=resolved)
            elif name in ("Gemini", "GitHubModels") and resolved == LARGE:
                result = fn(prompt, timeout=_TIMEOUT_LARGE)  # long drafts need more than 25 s
            else:
                result = fn(prompt)
        except Exception:
            # Transport errors are normally handled per model. An unexpected
            # provider exception must not expose its message or stop fallback.
            result = None
        if name in configured and _ATTEMPTS.get() > before_attempts:
            log_provider(name, bool(result), db_path)
        if result:
            try:  # sample emails or key-shaped strings in AI text must never reach the repository
                import secret_guard
                result = secret_guard.clean_ai_text(result)
            except Exception:
                pass
            return result
        if name in _RATE_LIMITED:
            set_cooldown(name, db_path=db_path)

    return None


def log_provider(name: str, ok: bool, db_path=None) -> None:
    """Count answers and failures per provider per day (for the daily Telegram report). Never raises."""
    path = _state_path(db_path)
    if isinstance(path, sqlite3.Connection):
        return  # implicit test logs must not write into the live database
    try:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE IF NOT EXISTS provider_log (day TEXT, provider TEXT, ok INTEGER DEFAULT 0, "
                     "fail INTEGER DEFAULT 0, PRIMARY KEY (day, provider))")
        col = "ok" if ok else "fail"
        conn.execute(f"INSERT INTO provider_log (day, provider, {col}) VALUES (?, ?, 1) "
                     f"ON CONFLICT(day, provider) DO UPDATE SET {col} = {col} + 1",
                     (datetime.datetime.now(datetime.timezone.utc).date().isoformat(), name))
        conn.commit()
        conn.close()
    except Exception:
        pass


@_router_scoped
@guardrails.budget_scoped
def chat_vision(image_bytes: bytes, mime_type: str, prompt: str = "Describe and analyze this image in detail.", *, db_path=None) -> str | None:
    """Analyze an image using Gemini vision capability via GEMINI_API_KEY or GEMINI_API_KEY_2.

    Returns str response or None if Gemini is unconfigured or fails.
    """
    if not guardrails.can_call_llm(db_path):
        print("LLM vision: skipped (kill switch on or daily call cap reached)", file=sys.stderr)
        return None

    primary_key = os.environ.get("GEMINI_API_KEY", "")
    secondary_key = os.environ.get("GEMINI_API_KEY_2", "")
    keys_to_try = [k for k in [primary_key, secondary_key] if k and not k.startswith("YOUR_")]
    if not keys_to_try:
        print("LLM vision: GEMINI_API_KEY not configured", file=sys.stderr)
        return None

    prompt = scrub(prompt)
    if GUARD_ENABLED:
        guard_reasons = _high_guard_reasons(prompt)
        if guard_reasons:
            return blocked_by_guard_result(guard_reasons)

    import base64
    b64_data = base64.b64encode(image_bytes).decode("ascii")

    # Tries OpenAI-compatible vision format first, then native REST API
    openai_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

    for api_key in keys_to_try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **_UA}
        for model in _gemini_models():
            # OpenAI style vision payload
            body = {
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_data}"}}
                    ]
                }],
                "temperature": 0.7
            }
            try:
                req = urllib.request.Request(openai_url, data=json.dumps(body).encode(), headers=headers)
                text, _ = _request_json(req, "Gemini", model, _TIMEOUT_LARGE, _chat_parse)
                if text:
                    log_provider("Gemini", True, db_path)
                    try:
                        import secret_guard
                        text = secret_guard.clean_ai_text(text)
                    except Exception:
                        pass
                    return text
            except Exception as exc:
                _log_fail("Gemini-Vision-OpenAI", model, _describe(exc))

            # Native Gemini REST payload fallback
            native_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            native_body = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": mime_type, "data": b64_data}}
                    ]
                }]
            }
            try:
                req = urllib.request.Request(native_url, data=json.dumps(native_body).encode(),
                                             headers={"Content-Type": "application/json", **_UA})
                text, _ = _request_json(req, "Gemini", model, _TIMEOUT_LARGE, _native_parse)
                if text:
                    log_provider("Gemini", True, db_path)
                    return secret_guard.clean_ai_text(text)
            except Exception as exc:
                _log_fail("Gemini-Vision-Native", model, _describe(exc))

    log_provider("Gemini", False, db_path)
    return None


@_router_scoped
@guardrails.budget_scoped
def chat_audio(audio_bytes: bytes, mime_type: str, prompt: str = "Transcribe and analyze this audio.", *, db_path=None) -> str | None:
    """Analyze/transcribe audio using Gemini audio capability via GEMINI_API_KEY or GEMINI_API_KEY_2.

    Returns str response or None if Gemini is unconfigured or fails.
    """
    if not guardrails.can_call_llm(db_path):
        print("LLM audio: skipped (kill switch on or daily call cap reached)", file=sys.stderr)
        return None

    primary_key = os.environ.get("GEMINI_API_KEY", "")
    secondary_key = os.environ.get("GEMINI_API_KEY_2", "")
    keys_to_try = [k for k in [primary_key, secondary_key] if k and not k.startswith("YOUR_")]
    if not keys_to_try:
        print("LLM audio: GEMINI_API_KEY not configured", file=sys.stderr)
        return None

    prompt = scrub(prompt)
    if GUARD_ENABLED:
        guard_reasons = _high_guard_reasons(prompt)
        if guard_reasons:
            return blocked_by_guard_result(guard_reasons)

    import base64
    b64_data = base64.b64encode(audio_bytes).decode("ascii")

    for api_key in keys_to_try:
        for model in _gemini_models():
            native_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            native_body = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": mime_type, "data": b64_data}}
                    ]
                }]
            }
            try:
                req = urllib.request.Request(native_url, data=json.dumps(native_body).encode(),
                                             headers={"Content-Type": "application/json", **_UA})
                text, _ = _request_json(req, "Gemini", model, _TIMEOUT_LARGE, _native_parse)
                if text:
                    log_provider("Gemini", True, db_path)
                    return secret_guard.clean_ai_text(text)
            except Exception as exc:
                _log_fail("Gemini-Audio-Native", model, _describe(exc))

    log_provider("Gemini", False, db_path)
    return None


def _provider_models(name):
    if name == "Groq":
        return list(dict.fromkeys(_groq_models(LARGE)))
    if name == "Gemini":
        return _gemini_models()
    if name == "Cloudflare":
        return _cloudflare_models()
    if name == "OpenRouter":
        return [os.environ.get("OPENROUTER_MODEL", "").strip()] if os.environ.get("OPENROUTER_MODEL") else []
    if name == "GitHubModels":
        return _github_models_list()
    return ["auto"] if name == "FreeLLMAPI" else []


def provider_status(db_path=None) -> list[dict]:
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
            key1 = os.environ.get("GEMINI_API_KEY", "")
            key2 = os.environ.get("GEMINI_API_KEY_2", "")
            configured = bool((key1 and not key1.startswith("YOUR_")) or (key2 and not key2.startswith("YOUR_")))
        elif name == "OpenRouter":
            key = os.environ.get("OPENROUTER_API_KEY", "")
            configured = bool(key and not key.startswith("YOUR_") and os.environ.get("OPENROUTER_MODEL", "").strip())
        elif name == "GitHubModels":
            configured = github_models_enabled()
        elif name == "Cloudflare":
            tok = os.environ.get("CF_API_TOKEN", "")
            acc = os.environ.get("CF_ACCOUNT_ID", "")
            configured = bool(tok and acc and not tok.startswith("YOUR_"))
        else:
            configured = False
        models = _provider_models(name)
        status.append({"provider": name, "configured": configured,
                       "models": [provider_state.health(name, model, _model_cap(name, model),
                                                        _state_path(db_path))
                                  for model in models if _public_model(model) == model]})
    return status


def get_provider_health(db_path=None, checked_at=None):
    """Public-safe latest observations, usable by /health and ai_health exports.

    Only allowlisted providers/model ids and fixed failure descriptions leave
    the router. This read never creates a missing database or sends a request.
    """
    checked_at = checked_at or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshots = provider_state.snapshots(_state_path(db_path))
    rows = []
    for name, _ in _PROVIDERS:
        observed = [(model, state) for provider, model, state in snapshots
                    if provider == name and _public_model(model) == model and state.get("observed_at")]
        row = {"provider": name, "model": None, "state": "unknown",
               "reason": "No activity recorded", "checked_at": checked_at, "failure_reasons": {}}
        if observed:
            model, state = max(observed, key=lambda item: item[1]["observed_at"])
            row["model"] = model
            for _, data in observed:
                for kind, count in data["failures"].items():
                    if kind in provider_state.REASONS and type(count) is int and count > 0:
                        prior = row["failure_reasons"].get(kind, {}).get("count", 0)
                        row["failure_reasons"][kind] = {"count": prior + count,
                                                        "reason": provider_state.REASONS[kind]}
            if state["observed_at"] <= time.time() - provider_state.RECENT_SECONDS:
                row["reason"] = "No recent activity recorded"
            elif state.get("last_ok"):
                row.update(state="up", reason="Latest request succeeded")
            else:
                row.update(state="down", reason=provider_state.REASONS.get(
                    state["last_kind"], "Provider request failed"))
        rows.append(row)
    return rows


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


def _native_parse(data):
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


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
        for model in _cloudflare_models():
            url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{model}"
            out.append(("Cloudflare", model, url, headers, _cf_parse))
    return [target for target in out if _public_model(target[1]) == target[1]]


@_router_scoped
@guardrails.budget_scoped
def health(probe=_probe, long_timeout=90, *, db_path=None):
    """Run one tiny test per provider/model, then one long-answer test on the first large model that works.

    Returns a list of result dicts. Nothing here contains a key: only provider, model, ok, reason, seconds.
    """
    results = []
    targets = _targets()

    def attempt(provider, model, url, headers, body, timeout, parse):
        if provider == "Cloudflare":
            body = {k: v for k, v in body.items() if k != "model"}
            body["max_tokens"] = 4096 if timeout == long_timeout else 64
        if probe is not _probe:
            # Injectable deterministic diagnostic for existing offline callers.
            # Production probes use the same accounting/transport as chat.
            return probe(url, headers, body, timeout, parse)
        started = time.time()
        if not guardrails.can_call_llm(db_path):
            return False, "Global call budget unavailable", 0
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        text, _ = _request_json(req, provider, model, timeout, parse)
        state = provider_state.health(provider, model, _model_cap(provider, model), _state_path(db_path))
        reason = "" if text else state["reason"] or "Provider skipped by budget or circuit"
        if not text and state["capped"]:
            reason = "Model daily cap reached"
        elif not text and state["breaker"] == "open":
            reason = "Circuit cooling down: " + (state["reason"] or "Provider request failed")
        return bool(text), reason, time.time() - started

    for provider, model, url, headers, parse in targets:
        body = {"model": model, "messages": [{"role": "user", "content": HEALTH_PROMPT}]}
        ok, reason, secs = attempt(provider, model, url, headers, body, _TIMEOUT, parse)
        results.append({"test": "tiny", "provider": provider, "model": model, "ok": ok,
                        "reason": reason, "seconds": round(secs, 1)})
    large = os.environ.get("GROQ_MODEL_LARGE", "openai/gpt-oss-120b")
    working = [r for r in results if r["ok"]]
    # Same order as real drafts: the Groq large model first, then every other model that answered.
    working.sort(key=lambda r: not (r["provider"] == "Groq" and r["model"] == large))
    for pick in working:
        target = next(t for t in targets if t[0] == pick["provider"] and t[1] == pick["model"])
        body = {"model": target[1], "messages": [{"role": "user", "content": LONG_PROMPT}]}
        ok, reason, secs = attempt(target[0], target[1], target[2], target[3], body, long_timeout, target[4])
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


def github_model_names(opener=None) -> list[str]:
    """Names of the GitHub Models chat models (public catalog ids only)."""
    token = _github_models_token()
    if not token:
        return []
    req = urllib.request.Request("https://models.github.ai/catalog/models",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/json", **_UA})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        return [f"(could not list models: {_describe(exc)})"]
    items = data if isinstance(data, list) else data.get("data", [])
    names = [str(m.get("id", "")) for m in items if isinstance(m, dict)]
    names = [n for n in names if re.fullmatch(r"[A-Za-z0-9_./:\-]{1,80}", n) and "mini" in n.lower()]
    return sorted(set(names))[:15]


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
        models = github_model_names()
        if models:
            print("GitHub Models (small ones) this token can use: " + ", ".join(models))
        if _github_models_token() and not github_models_enabled():
            print("GitHub Models is switched OFF for real work (repo variable GITHUB_MODELS is not 'on').")
    else:
        print("usage: python3 llm_router.py health")
