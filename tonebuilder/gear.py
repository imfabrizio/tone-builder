"""La tua strumentazione: chitarre (con pickup e accordatura) e dispositivi.

Per i nomi delle chitarre usiamo Wikidata, il database pubblico e aperto di
Wikimedia: la ricerca propone modelli reali (es. "Fender Telecaster") e ne
tiene l'identificativo. Per i pickup non esiste un database pubblico affidabile:
c'e' un elenco dei modelli piu' diffusi, con tipo e carattere, e il testo libero
resta sempre possibile.

Tutto e' salvato in ~/.tone-builder/gear.json.
"""
from __future__ import annotations

import json
import re
import uuid

import httpx
from pydantic import BaseModel, Field

from .config import app_home

PICKUP_CONFIGS = ["SSS", "HSS", "HSH", "HH", "SS", "H", "P90", "P90 P90", "Mini-HH", "Piezo", "Altro"]

# modello -> (tipo, carattere): elenco curato dei pickup piu' diffusi
PICKUPS: dict[str, tuple[str, str]] = {
    "Fender Custom Shop Fat '50s": ("single", "vintage, caldo"),
    "Fender Custom Shop '69": ("single", "vintage brillante"),
    "Fender Texas Special": ("single", "output medio-alto, medie piene"),
    "Fender Noiseless (Gen 4)": ("single", "silenzioso, moderno"),
    "Fender V-Mod II": ("single", "versatile"),
    "Fender Pure Vintage '59": ("single", "vintage"),
    "Fender Tex-Mex": ("single", "caldo, output medio"),
    "Fender Broadcaster (Tele)": ("single", "twang classico"),
    "Fender Shawbucker": ("humbucker", "output medio"),
    "Gibson Burstbucker 1/2/3": ("humbucker", "PAF vintage, output basso-medio"),
    "Gibson '57 Classic": ("humbucker", "PAF caldo"),
    "Gibson '57 Classic Plus": ("humbucker", "PAF con piu' output"),
    "Gibson 490R / 498T": ("humbucker", "output medio-alto, rock"),
    "Gibson 496R / 500T": ("humbucker", "output alto, ceramico"),
    "Gibson P-90": ("p90", "grintoso, medie"),
    "Seymour Duncan JB (SH-4)": ("humbucker", "output alto, medie avanti, rock"),
    "Seymour Duncan Jazz (SH-2)": ("humbucker", "chiaro, pulito, manico"),
    "Seymour Duncan '59 (SH-1)": ("humbucker", "PAF vintage"),
    "Seymour Duncan Pearly Gates (SH-PG1)": ("humbucker", "brillante, alla ZZ Top"),
    "Seymour Duncan Distortion (SH-6)": ("humbucker", "output molto alto"),
    "Seymour Duncan Invader (SH-8)": ("humbucker", "output estremo"),
    "Seymour Duncan Custom 5 (SH-14)": ("humbucker", "caldo, output alto"),
    "Seymour Duncan Alnico II Pro (APH-1)": ("humbucker", "morbido, vintage"),
    "Seymour Duncan SSL-1": ("single", "vintage"),
    "Seymour Duncan SSL-5": ("single", "output alto"),
    "Seymour Duncan Hot Rails (SHR-1)": ("humbucker-single-size", "output alto"),
    "Seymour Duncan Little '59": ("humbucker-single-size", "PAF in formato single"),
    "Seymour Duncan Antiquity": ("single", "vintage invecchiato"),
    "DiMarzio Super Distortion (DP100)": ("humbucker", "output molto alto"),
    "DiMarzio PAF 36th Anniversary (DP103)": ("humbucker", "PAF chiaro"),
    "DiMarzio Tone Zone (DP155)": ("humbucker", "bassi e medie piene"),
    "DiMarzio Air Norton (DP193)": ("humbucker", "manico, morbido"),
    "DiMarzio Evolution (DP158/DP159)": ("humbucker", "output alto, aggressivo"),
    "DiMarzio Area '58/'61/'67": ("single", "senza ronzio, vintage"),
    "DiMarzio Chopper": ("humbucker-single-size", "output alto"),
    "EMG 81": ("humbucker-active", "attivo, output alto, metal"),
    "EMG 85": ("humbucker-active", "attivo, caldo"),
    "EMG 60": ("humbucker-active", "attivo, chiaro"),
    "EMG SA": ("single-active", "attivo"),
    "EMG David Gilmour DG20": ("single-active", "attivo, sustain, alla Gilmour"),
    "Fishman Fluence Modern": ("humbucker-active", "attivo, multi-voce"),
    "Fishman Fluence Classic": ("humbucker-active", "attivo, PAF"),
    "Fishman Fluence Single Width": ("single-active", "attivo, multi-voce"),
    "Bare Knuckle The Mule": ("humbucker", "PAF"),
    "Bare Knuckle Nailbomb": ("humbucker", "output alto"),
    "Bare Knuckle Riff Raff": ("humbucker", "vintage AC/DC"),
    "Lollar Imperial": ("humbucker", "vintage"),
    "Mojotone '59 Clone": ("humbucker", "PAF"),
    "PRS 85/15": ("humbucker", "moderno, versatile"),
    "PRS 58/15 LT": ("humbucker", "vintage"),
    "Ibanez V7/V8": ("humbucker", "output alto"),
    "Ibanez Quantum": ("humbucker", "output medio-alto"),
    "Rickenbacker Toaster": ("single", "jangle chiaro"),
    "Gretsch Filter'Tron": ("humbucker", "chiaro, twang"),
    "Gibson Mini-humbucker": ("mini-humbucker", "chiaro"),
}


class Guitar(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str  # es. "Fender Stratocaster American Pro II"
    wikidata_id: str = ""
    config: str = ""  # es. HSS
    pickups: dict[str, str] = Field(default_factory=dict)  # posizione -> modello (manico/centrale/ponte)
    tuning: str = "standard (E)"
    notes: str = ""


class GearStore(BaseModel):
    guitars: list[Guitar] = Field(default_factory=list)
    devices: list[str] = Field(default_factory=lambda: ["Helix Stadium", "Quad Cortex"])
    other: str = ""  # altro: pedali reali, ampli, wah esterni...


def _path():
    return app_home() / "gear.json"


def load_gear() -> GearStore:
    p = _path()
    if p.exists():
        try:
            return GearStore.model_validate(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return GearStore()


def save_gear(g: GearStore) -> GearStore:
    _path().write_text(g.model_dump_json(indent=1), encoding="utf-8")
    return g


def describe_for_ai(g: GearStore) -> str:
    """Testo compatto con la strumentazione, da passare all'AI."""
    if not g.guitars and not g.other:
        return ""
    lines = []
    for gt in g.guitars:
        pk = ", ".join(f"{pos}: {model} ({PICKUPS.get(model, ('', ''))[1] or 'n.d.'})" for pos, model in gt.pickups.items() if model)
        lines.append(f"- {gt.name}" + (f" [{gt.config}]" if gt.config else "") + (f"; pickup {pk}" if pk else "")
                     + f"; accordatura {gt.tuning}" + (f"; note: {gt.notes}" if gt.notes else ""))
    if g.other:
        lines.append(f"- Altro: {g.other}")
    return "\n".join(lines)


def search_pickups(q: str, limit: int = 15) -> list[dict[str, str]]:
    words = re.findall(r"\w+", q.lower())
    out = []
    for name, (kind, char) in PICKUPS.items():
        hay = f"{name} {kind}".lower()
        if all(w in hay for w in words):
            out.append({"name": name, "description": f"{kind}, {char}", "source": "elenco"})
    return out[:limit]


_GUITAR_WORDS = ("guitar", "chitarra", "gitarre", "guitare")


def search_guitars(q: str, limit: int = 10) -> list[dict[str, str]]:
    """Modelli di chitarra da Wikidata (database pubblico di Wikimedia)."""
    if len(q.strip()) < 2:
        return []
    try:
        r = httpx.get(
            "https://www.wikidata.org/w/api.php",
            params={"action": "wbsearchentities", "search": q, "language": "en", "uselang": "it",
                    "type": "item", "limit": 30, "format": "json"},
            headers={"User-Agent": "ToneBuilder/0.1 (https://github.com/imfabrizio/tone-builder)"},
            timeout=8,
        )
        r.raise_for_status()
        items = r.json().get("search", [])
    except Exception:
        return []
    out = []
    for it in items:
        desc = (it.get("description") or "").lower()
        if any(w in desc for w in _GUITAR_WORDS):
            out.append({"name": it.get("label", ""), "description": it.get("description", ""),
                        "wikidata_id": it.get("id", ""), "source": "Wikidata"})
    return out[:limit]
