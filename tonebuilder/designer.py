"""Il "tone designer": dalla canzone + descrizione a una ricetta pronta.

Flusso:
1. (opzionale) ricerca web: strumentazione originale, preset e IR GRATUITI
   gia' esistenti, con i commenti degli utenti;
2. l'AI scrive la ricetta scegliendo solo modelli presenti nei cataloghi
   reali di Helix Stadium e Quad Cortex;
3. validazione; se qualcosa non torna, l'AI riceve l'errore e corregge.

Le modifiche successive ("piu' gain nel lead", "metti il delay sul FS3")
passano da `edit()`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from .ai.base import AIError, LLMProvider
from .catalogs.helix import HelixCatalog
from .catalogs.quadcortex import QCCatalog
from .knobs import KNOB_ALIASES
from .recipe import Role, ToneRecipe


class DesignRequest(BaseModel):
    title: str = ""
    artist: str = ""
    description: str = ""  # il suono che vuoi, a parole
    guitar: str = ""  # chitarra e pickup che userai
    snapshots: str = ""  # es. "Intro pulito, Strofa crunch, Assolo lead con delay"
    gear_preferences: str = ""  # es. "preferisco ampli Marshall, niente fuzz"
    audio_analysis: str = ""  # testo prodotto dall'analisi della traccia
    references: list[dict[str, Any]] = []  # ricette importate da usare come base


@dataclass
class DesignResult:
    recipe: ToneRecipe
    research: str = ""
    sources: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"recipe": self.recipe.model_dump(), "research": self.research,
                "sources": self.sources, "warnings": self.warnings}


RECIPE_FORMAT = """
FORMATO DELLA RICETTA (JSON):
{
  "name": "nome preset (max 32 caratteri)",
  "song": {"title": "", "artist": "", "year": null, "guitarist": "", "reference_gear": "strumentazione originale nota"},
  "guitar": {"description": "chitarra usata", "pickups": ""},
  "description": "una riga sul suono",
  "chain": [
    {
      "id": "id breve e unico, es. drive1, amp1, cab1, dly1, rev1",
      "role": "uno tra: gate, comp, wah, pitch, drive, amp, preamp, cab, ir, eq, mod, delay, reverb, volume, filter",
      "gear_ref": "ampli/pedale REALE di riferimento, es. 'Marshall JCM800 2203', 'Ibanez TS808'",
      "label": "etichetta corta per il footswitch, es. 'TS808'",
      "line": 1,
      "enabled": true,
      "settings": {"manopola": valore},
      "ir": null,
      "helix": {"model": "model_id ESATTO dal catalogo Helix", "params": {}},
      "qc": {"model": "id numerico o nome ESATTO dal catalogo Quad Cortex", "params": {}},
      "notes": "perche' questa scelta"
    }
  ],
  "snapshots": [
    {"name": "max 16 caratteri", "enabled": {"block_id": true/false}, "overrides": {"block_id": {"manopola": valore}}, "level_db": null, "notes": ""}
  ],
  "footswitches": [{"switch": "FS1".."FS10", "block_id": "id blocco", "label": ""}],
  "notes": ["consigli pratici per il chitarrista"]
}
"""

KNOB_RULES = f"""
MANOPOLE NEUTRE (campo "settings" e "overrides"):
- scala 0-10 come su un ampli vero: {', '.join(k for k in KNOB_ALIASES if not k.endswith(('_ms', '_db', '_hz')) and k != 'ratio')}
- in millisecondi: time_ms, predelay_ms, attack_ms; in dB: level_db, threshold_db; in Hz: low_cut_hz, high_cut_hz; ratio come numero.
- Usa SOLO queste chiavi. Il programma le traduce nei parametri nativi.
"""

RULES = """
REGOLE:
1. Scegli i modelli SOLO dai cataloghi forniti: helix.model = model_id esatto (prima colonna del catalogo Helix),
   qc.model = id numerico (prima colonna del catalogo QC). I modelli Helix con * sono gia' pronti nella libreria dell'utente: preferiscili a parita' di suono.
2. Ricrea il suono della canzone: strumentazione originale se nota, altrimenti l'equivalente piu' vicino.
3. Adatta il gain alla chitarra dell'utente (single coil vs humbucker).
4. Sfrutta i dispositivi: sono potenti. Hai DUE LINEE in serie ("line": 1 o 2), ognuna con un DSP suo:
   - linea 1 (max 8 blocchi): gate, compressore, wah, boost e drive, ampli (anche 2 ampli diversi, es. uno pulito
     e uno saturo accesi in snapshot diversi), cab/IR;
   - linea 2 (max 8 blocchi): EQ, modulazioni (chorus, flanger, phaser, tremolo), delay, riverberi, volume/boost finale.
   Una patch completa da live ha di solito 10-14 blocchi. Non lasciare la linea 2 vuota se il brano usa effetti.
   Ordine dei blocchi = ordine del segnale dentro ogni linea.
5. Uno snapshot per ogni sezione richiesta (max 8). Negli snapshot cambia solo cio' che serve:
   on/off dei blocchi in "enabled", valori diversi in "overrides". Usa level_db per bilanciare i volumi (es. +2 nell'assolo).
6. Se nella ricerca hai trovato preset o IR GRATUITI ben valutati, usali come riferimento per le impostazioni e citali nelle "notes".
   Mai risorse a pagamento.
7. Assegna ai footswitch i blocchi che il chitarrista vorra' attivare a mano (es. drive extra, delay).
8. Scrivi le note per il chitarrista in italiano.
"""


def _catalog_block() -> str:
    hx = HelixCatalog.load()
    qc = QCCatalog.load()
    return (
        "CATALOGO HELIX STADIUM (model_id | nome | categoria; * = gia' in libreria):\n"
        + hx.llm_listing()
        + "\n\nCATALOGO QUAD CORTEX (id | nome | categoria):\n"
        + (qc.llm_listing() or "(non disponibile: lascia qc.model vuoto, verra' scelto per somiglianza)")
    )


def _library_rule() -> str:
    if HelixCatalog.load().library_ready():
        return ("\nIMPORTANTE: per helix.model usa SOLO i modelli segnati con * (sono gli unici che il "
                "generatore di preset Helix sa costruire su questo computer). Se nessun modello * va bene per "
                "un ruolo, scegli il * piu' vicino e spiegalo nelle notes.\n")
    return ""


def system_prompt() -> str:
    return (
        "Sei un tecnico del suono esperto di chitarra e di multieffetti Line 6 Helix Stadium e "
        "Neural DSP Quad Cortex. Progetti preset pronti all'uso partendo da una canzone e da una descrizione.\n"
        + RECIPE_FORMAT + KNOB_RULES + RULES + _library_rule() + "\n" + _catalog_block()
    )


def research_prompt(req: DesignRequest) -> str:
    song = " - ".join(x for x in [req.artist, req.title] if x) or req.description
    return (
        f"Sto ricreando il suono di chitarra di: {song}.\n"
        f"Descrizione: {req.description}\n\n"
        "Cerca sul web e riassumi in italiano:\n"
        "1. Strumentazione originale del chitarrista per questo brano (ampli, pedali, chitarra, effetti) e come suona nelle varie parti.\n"
        "2. Preset GRATUITI gia' esistenti per Line 6 Helix/HX (es. CustomTone di Line 6) e per Neural DSP Quad Cortex "
        "(es. Cortex Cloud, forum, YouTube), con il link e cosa ne dicono gli utenti nei commenti (valutazioni, pregi, difetti).\n"
        "3. Impulse response (IR) GRATUITI adatti a questo suono, con link e giudizi degli utenti.\n"
        "Ignora tutto cio' che e' a pagamento. Indica le fonti."
    )


def _user_prompt(req: DesignRequest, research: str) -> str:
    parts = [
        f"CANZONE: {req.title or '-'} | ARTISTA: {req.artist or '-'}",
        f"SUONO RICHIESTO: {req.description or '-'}",
        f"CHITARRA DELL'UTENTE: {req.guitar or 'non specificata'}",
        f"SNAPSHOT RICHIESTI: {req.snapshots or 'decidi tu in base alla canzone'}",
    ]
    if req.gear_preferences:
        parts.append(f"PREFERENZE: {req.gear_preferences}")
    if req.audio_analysis:
        parts.append(f"ANALISI DELLA TRACCIA AUDIO:\n{req.audio_analysis}")
    if research:
        parts.append(f"RISULTATI DELLA RICERCA WEB:\n{research}")
    if req.references:
        parts.append("PRESET DI RIFERIMENTO IMPORTATI (usali come base):\n"
                     + json.dumps(req.references, ensure_ascii=False)[:20000])
    parts.append("Scrivi la ricetta JSON.")
    return "\n\n".join(parts)


def _validate(data: dict[str, Any], provider: LLMProvider, system: str, user: str,
              attempts: int = 2) -> ToneRecipe:
    last_err = ""
    for i in range(attempts + 1):
        try:
            return ToneRecipe.model_validate(data)
        except ValidationError as e:
            last_err = str(e)
            if i == attempts:
                break
            data = provider.complete_json(
                system,
                user + "\n\nLa ricetta che hai scritto non e' valida:\n" + last_err[:3000]
                + "\n\nRicetta precedente:\n" + json.dumps(data, ensure_ascii=False)[:15000]
                + "\n\nCorreggila e restituiscila completa.",
            )
    raise AIError(f"L'AI non e' riuscita a produrre una ricetta valida: {last_err[:500]}")


def check_models(recipe: ToneRecipe) -> list[str]:
    """Segnala modelli che non esistono nei cataloghi (i backend sceglieranno un equivalente)."""
    hx, qc = HelixCatalog.load(), QCCatalog.load()
    warnings = []
    for b in recipe.chain:
        if b.helix.model and not hx.get(b.helix.model):
            warnings.append(f"{b.id}: modello Helix '{b.helix.model}' non in catalogo")
        if b.qc.model and qc.models and not qc.get(b.qc.model):
            warnings.append(f"{b.id}: modello QC '{b.qc.model}' non in catalogo")
    return warnings


def design(req: DesignRequest, provider: LLMProvider, *, web_research: bool = True,
           audio_path: str | None = None) -> DesignResult:
    research_text, sources, warnings = "", [], []
    if web_research and provider.supports_web and (req.title or req.artist or req.description):
        try:
            rr = provider.research(research_prompt(req))
            research_text, sources = rr.text, rr.sources
        except AIError as e:
            warnings.append(f"Ricerca web non riuscita: {e}")
    system, user = system_prompt(), _user_prompt(req, research_text)
    use_audio = audio_path if getattr(provider, "supports_audio", False) else None
    if use_audio:
        user += "\n\nTi allego la traccia audio: ascoltala per capire gain, EQ, effetti e dinamica."
    data = provider.complete_json(system, user, audio_path=use_audio)
    recipe = _validate(data, provider, system, user)
    warnings += check_models(recipe)
    return DesignResult(recipe, research_text, sources, warnings)


EDIT_RULES = """
Ricevi una ricetta esistente e una richiesta di modifica in linguaggio naturale.
Applica SOLO la modifica richiesta, lasciando invariato il resto, e restituisci la ricetta completa.
Esempi: "piu' gain nell'assolo" -> overrides dello snapshot assolo; "metti il delay sul FS3" -> footswitches;
"il mix del riverbero cambia per snapshot" -> overrides del riverbero in ogni snapshot;
"togli il compressore" -> rimuovi il blocco e ogni riferimento negli snapshot/footswitch.
Aggiungi in "notes" una riga che spiega cosa hai cambiato.
"""


def edit(recipe: ToneRecipe, instruction: str, provider: LLMProvider) -> DesignResult:
    system = system_prompt() + "\n" + EDIT_RULES
    user = ("RICETTA ATTUALE:\n" + recipe.model_dump_json(indent=1)
            + "\n\nMODIFICA RICHIESTA: " + instruction + "\n\nRestituisci la ricetta JSON completa.")
    data = provider.complete_json(system, user)
    new = _validate(data, provider, system, user)
    return DesignResult(new, warnings=check_models(new))


ANALYSIS_PROMPT = """Ascolta questa traccia (chitarra, eventualmente gia' isolata) e descrivi in italiano, in modo tecnico:
- sezioni del brano e come cambia il suono di chitarra in ciascuna (con minutaggio approssimativo);
- quantita' di gain/saturazione, carattere dell'EQ (scuro/brillante, medie), dinamica;
- effetti udibili (delay: tempo stimato e ripetizioni; riverbero: tipo e quantita'; modulazioni; wah; pitch);
- tipo di ampli e pickup probabili.
Rispondi con un elenco puntato compatto."""


def describe_audio(provider: LLMProvider, audio_path: str, features_text: str = "") -> str:
    """Descrizione del suono: Gemini ascolta l'audio, gli altri ragionano sulle misure."""
    if getattr(provider, "supports_audio", False):
        return provider.complete_text("Sei un tecnico del suono esperto di chitarra.", ANALYSIS_PROMPT, audio_path=audio_path)
    if not features_text:
        return ""
    return provider.complete_text(
        "Sei un tecnico del suono esperto di chitarra.",
        "Dalle seguenti misure di una traccia di chitarra deduci gain, EQ ed effetti probabili, "
        "in un elenco puntato compatto in italiano:\n" + features_text,
    )


__all__ = ["DesignRequest", "DesignResult", "design", "edit", "describe_audio", "system_prompt", "Role"]
