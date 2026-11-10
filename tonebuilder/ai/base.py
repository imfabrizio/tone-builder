"""Interfaccia comune per i modelli AI.

Ogni provider implementa due cose:
- `complete_json`: risposta in JSON (per ricette e modifiche);
- `research`: risposta testuale con ricerca web, se il provider la offre
  (Gemini: Google Search; Claude: web search tool). Se non la offre,
  `supports_web` e' False e l'app salta la fase di ricerca.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class AIError(RuntimeError):
    pass


@dataclass
class ResearchResult:
    text: str
    sources: list[dict[str, str]] = field(default_factory=list)  # [{title, url}]


class LLMProvider(ABC):
    name = "base"
    supports_web = False

    def __init__(self, api_key: str, model: str, base_url: str = ""):
        if not api_key and self.name != "openai":
            raise AIError(f"Manca la chiave API per {self.name}: inseriscila nelle Impostazioni")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    @abstractmethod
    def complete_text(self, system: str, user: str, *, audio_path: str | None = None) -> str: ...

    def complete_json(self, system: str, user: str, *, audio_path: str | None = None) -> dict[str, Any]:
        sys2 = system + "\n\nRispondi SOLO con un oggetto JSON valido, senza testo prima o dopo."
        text = self.complete_text(sys2, user, audio_path=audio_path)
        try:
            return parse_json(text)
        except AIError:
            # spesso e' una risposta troncata: riprova chiedendo JSON compatto
            text = self.complete_text(sys2 + COMPACT_HINT, user, audio_path=audio_path)
            return parse_json(text)

    def research(self, question: str) -> ResearchResult:
        raise AIError(f"{self.name} non supporta la ricerca web in questa configurazione")

    def list_models(self) -> list[str]:
        return [self.model]


COMPACT_HINT = ("\nLa risposta precedente era incompleta: scrivi JSON compatto su una riga, senza spazi "
                "superflui, note brevi e senza campi vuoti.")


def parse_json(text: str) -> dict[str, Any]:
    """Estrae il primo oggetto JSON da una risposta (tollera ```json ...```)."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    start = t.find("{")
    if start < 0:
        raise AIError(f"L'AI non ha restituito JSON: {text[:200]}")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(t[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except json.JSONDecodeError as e:
                    raise AIError(f"JSON non valido dall'AI: {e}") from e
    raise AIError("JSON incompleto nella risposta dell'AI")
