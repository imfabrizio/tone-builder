"""Provider Anthropic Claude (pacchetto anthropic)."""
from __future__ import annotations

from .base import AIError, LLMProvider, ResearchResult


class ClaudeProvider(LLMProvider):
    name = "claude"
    supports_web = True
    supports_audio = False  # Claude non ascolta audio: usiamo l'analisi numerica della traccia

    def __init__(self, api_key: str, model: str, base_url: str = "", workspace_id: str = ""):
        super().__init__(api_key, model or "claude-sonnet-5-5", base_url)
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise AIError("Pacchetto anthropic non installato: pip install anthropic") from e
        kw: dict = {}
        if base_url:
            kw["base_url"] = base_url
        if workspace_id.strip():
            kw["default_headers"] = {"anthropic-workspace-id": workspace_id.strip()}
        self._client = anthropic.Anthropic(api_key=api_key, **kw)

    @staticmethod
    def _explain(e: Exception) -> str:
        msg = str(e)
        if "anthropic-workspace-id" in msg:
            return ("la chiave non e' legata a un workspace. Crea una chiave dentro un workspace nella "
                    "Claude Console, oppure indica l'ID del workspace nelle Impostazioni.")
        return msg

    def complete_text(self, system: str, user: str, *, audio_path: str | None = None) -> str:
        try:
            r = self._client.messages.create(
                model=self.model, max_tokens=8000, system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as e:
            raise AIError(f"Claude: {self._explain(e)}") from e
        return "".join(getattr(b, "text", "") for b in r.content)

    def research(self, question: str) -> ResearchResult:
        try:
            r = self._client.messages.create(
                model=self.model, max_tokens=4000,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
                messages=[{"role": "user", "content": question}],
            )
        except Exception as e:
            raise AIError(f"Claude (ricerca): {self._explain(e)}") from e
        text, sources, seen = [], [], set()
        for b in r.content:
            if b.type == "text":
                text.append(b.text)
                for c in getattr(b, "citations", None) or []:
                    url = getattr(c, "url", None)
                    if url and url not in seen:
                        seen.add(url)
                        sources.append({"title": getattr(c, "title", None) or url, "url": url})
            elif b.type == "web_search_tool_result" and isinstance(getattr(b, "content", None), list):
                for res in b.content:
                    url = getattr(res, "url", None)
                    if url and url not in seen:
                        seen.add(url)
                        sources.append({"title": getattr(res, "title", None) or url, "url": url})
        return ResearchResult("".join(text), sources)

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self._client.models.list(limit=100))
        except Exception as e:
            raise AIError(f"Claude: impossibile elencare i modelli: {self._explain(e)}") from e
