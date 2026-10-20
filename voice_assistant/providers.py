"""Model provider selection: local server, Ollama, OpenAI, Claude, or any custom OpenAI-compatible endpoint. API keys live in Windows Credential Manager."""
from __future__ import annotations

import os

from .llm import LLM, AnthropicClient, OpenAICompatClient

SERVICE = "VoiceAssistant"
PROVIDERS = {"local": "Local (LM Studio / llama.cpp)", "ollama": "Ollama", "openai": "OpenAI", "anthropic": "Claude (Anthropic)",
             "custom": "Custom (OpenAI-compatible)"}
MODEL_FIELD = {"local": "llm_model", "ollama": "ollama_model", "openai": "openai_model", "anthropic": "anthropic_model", "custom": "custom_model"}
URL_FIELD = {"local": "llm_base_url", "ollama": "ollama_base_url", "openai": "openai_base_url", "custom": "custom_base_url"}  # anthropic has a fixed URL


def key_env(cfg, provider: str) -> str:
    return {"openai": cfg.openai_key_env, "anthropic": cfg.anthropic_key_env, "custom": cfg.custom_key_env}.get(provider, "")


def needs_key(provider: str) -> bool:
    return provider in ("openai", "anthropic")  # custom endpoints may or may not need one; local ones never do


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
    return getattr(cfg, MODEL_FIELD[provider])


def set_model(cfg, provider: str, model: str) -> None:
    setattr(cfg, MODEL_FIELD[provider], model.strip())


def make_llm(cfg) -> LLM:
    p = cfg.llm_provider
    if p == "openai":
        return OpenAICompatClient(cfg.openai_base_url, cfg.openai_model, get_key("openai", cfg.openai_key_env))
    if p == "anthropic":
        return AnthropicClient(cfg.anthropic_model, get_key("anthropic", cfg.anthropic_key_env))
    if p == "ollama":
        return OpenAICompatClient(cfg.ollama_base_url, cfg.ollama_model, "ollama")  # Ollama ignores the key but the header must be present
    if p == "custom":
        return OpenAICompatClient(cfg.custom_base_url, cfg.custom_model, get_key("custom", cfg.custom_key_env))
    return OpenAICompatClient(cfg.llm_base_url, cfg.llm_model, cfg.api_key(), extra_body=cfg.llm_extra_body)
