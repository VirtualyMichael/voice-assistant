"""Model provider selection: local server, OpenAI, or Claude. API keys live in Windows Credential Manager."""
from __future__ import annotations

import os

from .llm import LLM, AnthropicClient, OpenAICompatClient

SERVICE = "VoiceAssistant"
PROVIDERS = {"local": "Local (LM Studio / Ollama / llama.cpp)", "openai": "OpenAI", "anthropic": "Claude (Anthropic)"}


def get_key(provider: str, env_name: str = "") -> str:
    """Saved key first (Credential Manager), then the named environment variable."""
    try:
        import keyring

        key = keyring.get_password(SERVICE, provider)
        if key:
            return key
    except Exception:
        pass
    return os.environ.get(env_name, "") if env_name else ""


def save_key(provider: str, key: str) -> None:
    import keyring

    keyring.set_password(SERVICE, provider, key.strip())


def model_for(cfg, provider: str | None = None) -> str:
    provider = provider or cfg.llm_provider
    return {"local": cfg.llm_model, "openai": cfg.openai_model, "anthropic": cfg.anthropic_model}[provider]


def set_model(cfg, provider: str, model: str) -> None:
    setattr(cfg, {"local": "llm_model", "openai": "openai_model", "anthropic": "anthropic_model"}[provider], model.strip())


def make_llm(cfg) -> LLM:
    p = cfg.llm_provider
    if p == "openai":
        return OpenAICompatClient(cfg.openai_base_url, cfg.openai_model, get_key("openai", cfg.openai_key_env))
    if p == "anthropic":
        return AnthropicClient(cfg.anthropic_model, get_key("anthropic", cfg.anthropic_key_env))
    return OpenAICompatClient(cfg.llm_base_url, cfg.llm_model, cfg.api_key(), extra_body=cfg.llm_extra_body)
