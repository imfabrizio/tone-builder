"""Provider compatibile con l'API OpenAI (OpenAI, Ollama, LM Studio, OpenRouter...).

Basta indicare base_url e modello: per un modello locale con Ollama,
base_url = http://localhost:11434/v1 e nessuna chiave.
"""
from __future__ import annotations

import httpx

from .base import AIError, LLMProvider


class OpenAICompatProvider(LLMProvider):
    name = "openai"
    supports_web = False
    supports_audio = False

    def __init__(self, api_key: str, model: str, base_url: str = ""):
        super().__init__(api_key, model, base_url or "https://api.openai.com/v1")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def complete_text(self, system: str, user: str, *, audio_path: str | None = None) -> str:
        body = {
            "model": self.model,
            "temperature": 0.4,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            r = httpx.post(f"{self.base_url.rstrip('/')}/chat/completions", json=body,
                           headers=self._headers, timeout=180)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"] or ""
        except Exception as e:
            raise AIError(f"{self.name}: {e}") from e

    def list_models(self) -> list[str]:
        try:
            r = httpx.get(f"{self.base_url.rstrip('/')}/models", headers=self._headers, timeout=30)
            r.raise_for_status()
            return sorted(m["id"] for m in r.json().get("data", []))
        except Exception as e:
            raise AIError(f"{self.name}: impossibile elencare i modelli ({e})") from e
