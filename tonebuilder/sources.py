"""Risorse gratuite: IR e preset.

Due fonti:
1. le tue cartelle locali (IR .wav, preset .hlx/.hsp scaricati): indicizzate
   e proposte per nome/parole chiave;
2. il web, tramite la fase di ricerca dell'AI (designer.research_prompt),
   che riporta link e commenti degli utenti, solo risorse gratuite.

CustomTone (Line 6) richiede il login: non automatizziamo l'accesso al sito
con le tue credenziali. Scarichi tu i preset con il tuo account (i link di
ricerca li prepara l'app) e li salvi in una cartella "preset": l'app li vede
e li importa da li'.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote_plus


@dataclass
class LocalResource:
    kind: str  # ir | preset
    name: str
    path: str
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def scan(folders: list[str], kind: str) -> list[LocalResource]:
    exts = {".wav"} if kind == "ir" else {".hlx", ".hsp", ".json"}
    out = []
    for f in folders:
        root = Path(f).expanduser()
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if p.suffix.lower() in exts and p.is_file():
                out.append(LocalResource(kind, p.stem, str(p)))
    return out


def rank(resources: list[LocalResource], query: str, limit: int = 20) -> list[LocalResource]:
    q = _words(query)
    for r in resources:
        hay = _words(r.name + " " + str(Path(r.path).parent.name))
        r.score = len(q & hay)
    hits = [r for r in resources if r.score > 0]
    hits.sort(key=lambda r: (-r.score, r.name))
    return hits[:limit]


def search_links(artist: str, title: str) -> list[dict[str, str]]:
    """Link di ricerca pronti da aprire nel browser (con il tuo login dove serve)."""
    q = " ".join(x for x in [artist, title] if x).strip()
    if not q:
        return []
    qp = quote_plus(q)
    return [
        {"title": f"CustomTone (Line 6) – {q}", "url": f"https://www.google.com/search?q=site%3Aline6.com%2Fcustomtone+{qp}"},
        {"title": f"Cortex Cloud / Quad Cortex – {q}", "url": f"https://www.google.com/search?q=quad+cortex+preset+{qp}"},
        {"title": f"Helix preset gratuiti – {q}", "url": f"https://www.google.com/search?q=helix+preset+free+{qp}"},
        {"title": f"YouTube: tone {q}", "url": f"https://www.youtube.com/results?search_query={qp}+guitar+tone+helix+OR+quad+cortex"},
        {"title": "IR gratuiti per chitarra", "url": "https://www.google.com/search?q=free+guitar+cabinet+impulse+responses"},
    ]
