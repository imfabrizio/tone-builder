"""Provider Google Gemini (pacchetto google-genai).

Gemini puo' anche ASCOLTARE l'audio: per l'analisi del suono gli passiamo la
traccia (meglio se gia' separata con Demucs) e gli chiediamo di descriverla.
"""
from __future__ import annotations

from .base import AIError, LLMProvider, ResearchResult


class GeminiProvider(LLMProvider):
    name = "gemini"
    supports_web = True
    supports_audio = True

    def __init__(self, api_key: str, model: str, base_url: str = ""):
        super().__init__(api_key, model or "gemini-2.5-pro", base_url)
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:  # pragma: no cover
            raise AIError("Pacchetto google-genai non installato: pip install google-genai") from e
        self._genai, self._types = genai, types
        self._client = genai.Client(api_key=api_key)

    def _contents(self, user: str, audio_path: str | None):
        if not audio_path:
            return user
        f = self._client.files.upload(file=audio_path)
        return [f, user]

    def complete_text(self, system: str, user: str, *, audio_path: str | None = None) -> str:
        cfg = self._types.GenerateContentConfig(system_instruction=system, temperature=0.4,
                                                max_output_tokens=32768)
        try:
            r = self._client.models.generate_content(model=self.model, contents=self._contents(user, audio_path), config=cfg)
        except Exception as e:
            raise AIError(f"Gemini: {e}") from e
        return r.text or ""

    def complete_json(self, system: str, user: str, *, audio_path: str | None = None):
        from .base import COMPACT_HINT, parse_json

        cfg = self._types.GenerateContentConfig(
            system_instruction=system, temperature=0.4, response_mime_type="application/json",
            max_output_tokens=32768,
        )
        contents = self._contents(user, audio_path)
        try:
            r = self._client.models.generate_content(model=self.model, contents=contents, config=cfg)
            try:
                return parse_json(r.text or "")
            except AIError:  # risposta troncata: riprova chiedendo JSON compatto
                cfg.system_instruction = system + COMPACT_HINT
                r = self._client.models.generate_content(model=self.model, contents=contents, config=cfg)
        except AIError:
            raise
        except Exception as e:
            raise AIError(f"Gemini: {e}") from e
        return parse_json(r.text or "")

    def research(self, question: str) -> ResearchResult:
        t = self._types
        cfg = t.GenerateContentConfig(tools=[t.Tool(google_search=t.GoogleSearch())], temperature=0.3)
        try:
            r = self._client.models.generate_content(model=self.model, contents=question, config=cfg)
        except Exception as e:
            raise AIError(f"Gemini (ricerca): {e}") from e
        sources = []
        try:
            for ch in r.candidates[0].grounding_metadata.grounding_chunks or []:
                if ch.web and ch.web.uri:
                    sources.append({"title": ch.web.title or ch.web.domain or ch.web.uri, "url": ch.web.uri})
        except (AttributeError, IndexError, TypeError):
            pass
        return ResearchResult(r.text or "", sources)

    def list_models(self) -> list[str]:
        try:
            out = []
            for m in self._client.models.list():
                actions = getattr(m, "supported_actions", None) or []
                if not actions or "generateContent" in actions:
                    out.append(m.name.removeprefix("models/"))
            return sorted(out)
        except Exception as e:
            raise AIError(f"Gemini: impossibile elencare i modelli ({e})") from e
