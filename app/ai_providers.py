"""AI provider hub — pick the brain behind J.A.R.V.I.S.

Supported providers: gemini (native voice session), openai, anthropic,
groq, ollama (local) and fully custom OpenAI-compatible endpoints.

Only `gemini` drives the real-time voice session. Every other provider
handles typed text commands through a plain HTTP chat-completion call, so
an API key + model is all it takes — no extra dependencies (requests only).
"""
from __future__ import annotations


GEMINI_DEFAULT_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"

PROVIDERS: dict[str, dict] = {
    "gemini": {
        "label": "Google Gemini (voice + text)",
        "models": [
            GEMINI_DEFAULT_MODEL,
            "models/gemini-2.0-flash-001",
            "models/gemini-1.5-pro-002",
        ],
        "needs_key": True,
        "key_field": "gemini_api_key",
    },
    "openai": {
        "label": "OpenAI",
        "endpoint": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "o3-mini", "gpt-4.1-mini"],
        "needs_key": True,
        "key_field": "openai_api_key",
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "endpoint": "https://api.anthropic.com/v1",
        "models": ["claude-sonnet-4-20250514", "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"],
        "needs_key": True,
        "key_field": "anthropic_api_key",
    },
    "groq": {
        "label": "Groq (fast + free tier)",
        "endpoint": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
        "needs_key": True,
        "key_field": "groq_api_key",
    },
    "ollama": {
        "label": "Ollama (local, no key)",
        "endpoint": "http://localhost:11434/v1",
        "models": ["llama3.1", "qwen2.5", "mistral", "phi3"],
        "needs_key": False,
        "key_field": "",
    },
    "custom": {
        "label": "Custom (OpenAI-compatible)",
        "endpoint": "",
        "models": [],
        "needs_key": True,
        "key_field": "custom_ai_api_key",
    },
}


def provider_names() -> list[str]:
    return list(PROVIDERS)


def models_for(provider: str) -> list[str]:
    return list(PROVIDERS.get(provider, {}).get("models", []))


def active_config(config: dict) -> dict:
    """Resolve provider + model + endpoint + key from the app config."""
    provider = str(config.get("ai_provider", "gemini") or "gemini").strip().lower()
    if provider not in PROVIDERS:
        provider = "gemini"
    meta = PROVIDERS[provider]
    if provider == "gemini":
        return {
            "provider": provider,
            "model": str(config.get("ai_model") or GEMINI_DEFAULT_MODEL).strip(),
            "endpoint": "",
            "api_key": str(config.get("gemini_api_key", "") or "").strip(),
        }
    if provider == "custom":
        return {
            "provider": provider,
            "model": str(config.get("custom_ai_model", "") or "").strip(),
            "endpoint": str(config.get("custom_ai_base_url", "") or "").strip().rstrip("/"),
            "api_key": str(config.get("custom_ai_api_key", "") or "").strip(),
            "name": str(config.get("custom_provider_name", "") or "Custom").strip(),
        }
    key_field = meta.get("key_field", "")
    return {
        "provider": provider,
        "model": str(config.get("ai_model") or (meta["models"][0] if meta["models"] else "")).strip(),
        "endpoint": str(config.get(f"{provider}_base_url") or meta.get("endpoint", "")).strip().rstrip("/"),
        "api_key": str(config.get(key_field, "") or "").strip(),
    }


def _openai_chat(endpoint: str, api_key: str, model: str, message: str, timeout: int = 60) -> str:
    import requests
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = requests.post(
        endpoint.rstrip("/") + "/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "You are JARVIS, a concise refined British executive assistant. Always answer in English."},
                {"role": "user", "content": message},
            ],
            "temperature": 0.7,
        },
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return str(data["choices"][0]["message"]["content"])


def _anthropic_chat(api_key: str, model: str, message: str, timeout: int = 60) -> str:
    import requests
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        json={
            "model": model,
            "max_tokens": 1024,
            "system": "You are JARVIS, a concise refined British executive assistant. Always answer in English.",
            "messages": [{"role": "user", "content": message}],
        },
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    blocks = data.get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()


def chat(config: dict, message: str, timeout: int = 60) -> str:
    """One-shot text answer from the configured non-Gemini provider."""
    active = active_config(config)
    provider = active["provider"]
    if provider == "gemini":
        raise ValueError("Gemini uses the live voice session — no HTTP call needed.")
    model = active.get("model", "")
    if not model:
        raise ValueError(f"No model selected for provider '{provider}'. Pick one in Control Center → AI.")
    if provider == "anthropic":
        if not active.get("api_key"):
            raise ValueError("Anthropic API key is missing — add it in Control Center → AI.")
        return _anthropic_chat(active["api_key"], model, message, timeout)
    endpoint = active.get("endpoint", "")
    if not endpoint.startswith(("http://", "https://")):
        raise ValueError(
            f"Provider '{provider}' has no valid endpoint URL — set it in Control Center → AI."
        )
    meta = PROVIDERS.get(provider, {})
    if meta.get("needs_key") and not active.get("api_key"):
        raise ValueError(f"API key for '{provider}' is missing — add it in Control Center → AI.")
    return _openai_chat(endpoint, active.get("api_key", ""), model, message, timeout)


def test_connection(config: dict) -> str:
    """Send a tiny probe message; returns the provider's reply (raises on failure)."""
    active = active_config(config)
    if active["provider"] == "gemini":
        if not active.get("api_key"):
            raise ValueError("Gemini API key is missing.")
        return f"Gemini voice model ready: {active['model']}"
    return chat(config, "Reply with exactly: link OK", timeout=30)
