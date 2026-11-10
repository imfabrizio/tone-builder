"""Scelta del provider AI in base alle impostazioni."""
from __future__ import annotations

from ..config import Settings
from .base import AIError, LLMProvider, ResearchResult

PROVIDERS = ("gemini", "claude", "openai")


def get_provider(settings: Settings, name: str | None = None) -> LLMProvider:
    name = name or settings.provider
    ps = settings.providers.get(name)
    if ps is None:
        raise AIError(f"Provider sconosciuto: {name}")
    if name == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider(ps.api_key, ps.model, ps.base_url)
    if name == "claude":
        from .claude import ClaudeProvider

        return ClaudeProvider(ps.api_key, ps.model, ps.base_url, workspace_id=ps.workspace_id)
    if name == "openai":
        from .openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(ps.api_key, ps.model, ps.base_url)
    raise AIError(f"Provider sconosciuto: {name}")


__all__ = ["AIError", "LLMProvider", "ResearchResult", "get_provider", "PROVIDERS"]
