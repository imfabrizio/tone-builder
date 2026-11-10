"""Impostazioni dell'app.

Il file vive FUORI dal repository, nella cartella utente
(~/.tone-builder/settings.yaml), cosi' le chiavi API non finiscono mai su
GitHub. Le variabili d'ambiente hanno la precedenza:
GEMINI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, TONEBUILDER_HOME.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


def app_home() -> Path:
    p = Path(os.environ.get("TONEBUILDER_HOME", Path.home() / ".tone-builder"))
    p.mkdir(parents=True, exist_ok=True)
    return p


class ProviderSettings(BaseModel):
    api_key: str = ""
    model: str = ""
    base_url: str = ""  # solo per provider compatibili OpenAI (OpenAI, Ollama, LM Studio...)


class Settings(BaseModel):
    provider: str = "gemini"  # gemini | claude | openai
    providers: dict[str, ProviderSettings] = Field(default_factory=lambda: {
        "gemini": ProviderSettings(model="gemini-2.5-pro"),
        "claude": ProviderSettings(model="claude-sonnet-5-5"),
        "openai": ProviderSettings(model="gpt-4o", base_url="https://api.openai.com/v1"),
    })
    web_research: bool = True  # lascia all'AI la ricerca web (preset/IR free, commenti)
    ir_folders: list[str] = Field(default_factory=list)  # cartelle locali con IR .wav
    preset_folders: list[str] = Field(default_factory=list)  # cartelle con .hlx/.hsp scaricati
    helix_output_dir: str = ""  # dove salvare i .hsp generati
    qc_target_setlist_slot: str = ""  # es. "30A": primo slot libero da usare sul QC
    language: str = "it"

    def active(self) -> ProviderSettings:
        return self.providers.get(self.provider, ProviderSettings())


_ENV_KEYS = {"gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


def settings_path() -> Path:
    return app_home() / "settings.yaml"


def load_settings() -> Settings:
    data: dict[str, Any] = {}
    p = settings_path()
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    s = Settings.model_validate(data)
    defaults = Settings().providers
    for name, d in defaults.items():
        s.providers.setdefault(name, d)
    for name, env in _ENV_KEYS.items():
        if os.environ.get(env):
            s.providers[name].api_key = os.environ[env]
    if not s.helix_output_dir:
        s.helix_output_dir = str(app_home() / "output" / "helix")
    return s


def save_settings(s: Settings) -> None:
    settings_path().write_text(
        yaml.safe_dump(s.model_dump(), allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def public_view(s: Settings) -> dict[str, Any]:
    """Versione per l'interfaccia: le chiavi non escono mai in chiaro."""
    d = s.model_dump()
    for name, p in d["providers"].items():
        key = p.get("api_key") or ""
        p["api_key"] = ""
        p["has_key"] = bool(key)
        p["key_hint"] = f"…{key[-4:]}" if len(key) >= 8 else ""
    return d
