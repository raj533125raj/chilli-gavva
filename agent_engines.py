"""Allowlisted per-agent routing through the existing router; no new providers."""
import os
import re

import guardrails
import llm_router
import secret_guard


def credential_like(text):
    """Reject credentials, including exact configured values, before persistence."""
    if not isinstance(text, str):
        return False
    return (secret_guard.scrub_text(text) != text or bool(re.search(
        r"api[\s_-]?key|access[\s_-]?token|secret|password|credential|private[\s_-]?key|bearer\s"
        r"|\b(?:sk-(?:proj-|ant-)?|gh[pousr]_|github_pat_|gsk_|xox[baprs]-|freellmapi-)[\w-]{8,}"
        r"|\bAKIA[0-9A-Z]{16}\b|\beyJ[\w-]{10,}\."
        r"|-----BEGIN|\bssh-(?:rsa|ed25519)\s|https?://[^\s/@:]+:[^\s/@]+@"
        r"|\b\d{6,}:[\w-]{20,}|\b[0-9a-f]{32,}\b"
        r"|\b(?=[A-Za-z0-9_+/=-]{40,}\b)(?=[A-Za-z0-9_+/=-]*[A-Za-z])"
        r"(?=[A-Za-z0-9_+/=-]*[0-9])[A-Za-z0-9_+/=-]{40,}", text, re.I)))


def choices():
    """Model IDs from router configuration only; never keys, URLs or a vendor catalogue."""
    providers = {name for name, _ in llm_router._PROVIDERS}
    candidates = {
        "Groq": llm_router._groq_models(llm_router.LARGE),
        "Gemini": llm_router._gemini_models(),
        "Cloudflare": ["llama-3.1-8b-instruct"],
        "OpenRouter": [os.environ.get("OPENROUTER_MODEL", "").strip()],
        "FreeLLMAPI": ["auto"],
        "GitHubModels": llm_router._github_models_list(),
    }
    return [(provider, model) for provider, models in candidates.items() if provider in providers
            for model in dict.fromkeys(models)
            if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9@][A-Za-z0-9@._:/-]{0,59}", model)
            and not credential_like(model) and (provider != "OpenRouter" or model.endswith(":free"))]


def modes():
    providers = {provider for provider, _ in choices()}
    return (["local"] if "FreeLLMAPI" in providers else []) + (["cloud"] if providers - {"FreeLLMAPI"} else [])


@guardrails.budget_scoped
def chat(prompt, *, db_path=None, size=None, mode="cloud", provider=None, model=None):
    """Honor the selected route and existing budget, injection, cooldown and output guards.

    No cross-engine fallback: an unavailable selection holds work until it is usable.
    Router functions reserve each actual attempt using the scoped database.
    """
    if not guardrails.can_call_llm(db_path):
        return None
    prompt = llm_router.scrub(prompt)
    if llm_router.GUARD_ENABLED:
        reasons = llm_router._high_guard_reasons(prompt)
        if reasons:
            return llm_router.blocked_by_guard_result(reasons)
    allowed = choices()
    if provider is not None:
        selected = [(provider, model)] if (provider, model) in allowed else []
    else:
        selected = [(p, m) for p, m in allowed if (p == "FreeLLMAPI") == (mode == "local")]
        if size != llm_router.LARGE:
            small = llm_router._groq_models(llm_router.SMALL)[0]
            selected = [(p, m) for p, m in selected if p != "Groq" or m == small]
    targets = {}
    for p, m, url, headers, _ in llm_router._targets():
        targets.setdefault((p, m), []).append((url, headers))
    # _targets() is the router's health-probe list and omits Gemini's backup key.
    # Runtime Gemini supports that key even when the primary is absent.
    backup = os.environ.get("GEMINI_API_KEY_2", "")
    if backup and not backup.startswith("YOUR_"):
        for p, m in allowed:
            if p == "Gemini":
                targets.setdefault((p, m), []).append((
                    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                    {"Authorization": "Bearer " + backup}))
    configured = {row["provider"] for row in llm_router.provider_status() if row["configured"]}
    for name, selected_model in selected:
        if name not in configured or llm_router.in_cooldown(name, db_path):
            continue
        llm_router._RATE_LIMITED.discard(name)
        if name == "FreeLLMAPI":
            answer = llm_router._freellmapi(prompt)
        elif name == "Cloudflare":
            answer = llm_router._cloudflare(prompt)
        elif (name, selected_model) in targets:
            answer = None
            for url, headers in targets[name, selected_model]:
                key = headers["Authorization"].removeprefix("Bearer ")
                answer, _ = llm_router._openai_style_call(
                    url, key, selected_model, prompt, name,
                    extra_headers={k: v for k, v in headers.items() if k != "Authorization"},
                    timeout=llm_router._TIMEOUT_LARGE if size == llm_router.LARGE else llm_router._TIMEOUT)
                if answer:
                    llm_router._RATE_LIMITED.discard(name)
                    break
        else:
            answer = None
        llm_router.log_provider(name, bool(answer), db_path)
        if name in llm_router._RATE_LIMITED:
            llm_router.set_cooldown(name, db_path=db_path)
        if answer:
            return secret_guard.clean_ai_text(answer)
    return None
