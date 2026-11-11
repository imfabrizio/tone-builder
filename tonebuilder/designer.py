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
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from .ai.base import AIError, LLMProvider
from .catalogs.helix import HelixCatalog
from .catalogs.quadcortex import QCCatalog
from .knobs import KNOB_ALIASES
from .diff import diff_recipes
from .recipe import Role, ToneRecipe


class DesignRequest(BaseModel):
    title: str = ""
    artist: str = ""
    description: str = ""  # il suono che vuoi, a parole
    guitar: str = ""  # chitarra e pickup che userai
    snapshots: str = ""  # es. "Intro pulito, Strofa crunch, Assolo lead con delay"
    gear_preferences: str = ""  # es. "preferisco ampli Marshall, niente fuzz"
    audio_analysis: str = ""  # testo prodotto dall'analisi della traccia
    gear: str = ""  # strumentazione disponibile (chitarre, pickup, accordature)
    references: list[dict[str, Any]] = []  # ricette importate da usare come base
    use_iridium: bool = False  # ampli e cab della linea 1 dallo Strymon Iridium (solo Helix)
    irs: list[str] = []  # nomi dei file IR dell'utente (dalle cartelle IR delle impostazioni)


@dataclass
class DesignResult:
    recipe: ToneRecipe
    research: str = ""
    sources: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reply: str = ""
    changes: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"recipe": self.recipe.model_dump(), "research": self.research,
                "sources": self.sources, "warnings": self.warnings, "reply": self.reply, "changes": self.changes}


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
- level dei blocchi: 5 = volume invariato (unity), ogni punto ~1.2 dB. Pedali drive usati come boost: level 6-8.
  Modulazioni, delay, riverberi: level 5 (il loro volume si regola con mix).
"""

GAIN_RULES = """
GAIN E VOLUMI (importante: i preset devono suonare pieni e alla pari, pronti per il live):
- gain dell'ampli (scala 0-10): pulito 2-4, crunch 5-6.5, ritmica distorta 6.5-8, lead 8-9.5. Per rock e alternative
  anni '90 la ritmica distorta e' piena e satura, non "soft": non stare bassi.
- drive/overdrive davanti all'ampli: come boost drive 1-3 e level 7-8; come distorsione vera drive 6-9.
- channel_volume e master dell'ampli 6-8, presence 5-7.
- mix: chorus 4-6, delay 2-3.5 (assolo fino a 4.5), riverbero 2-3.5.
- level_db degli snapshot per pareggiare i volumi: 0 come riferimento, puliti +1/+2 (suonano piu' piano),
  assolo +2/+3 rispetto alla ritmica.
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
   MAI piu' di 2 ampli (amp o preamp) per linea: e' un limite rigido.
   Una patch completa da live ha di solito 10-14 blocchi. Non lasciare la linea 2 vuota se il brano usa effetti.
   Ordine dei blocchi = ordine del segnale dentro ogni linea.
5. Uno snapshot per ogni sezione richiesta (max 8). Negli snapshot cambia solo cio' che serve:
   on/off dei blocchi in "enabled", valori diversi in "overrides". Usa level_db per bilanciare i volumi (es. +2 nell'assolo).
6. Se la ricerca riporta preset o IR GRATUITI ben valutati, usali come riferimento per le impostazioni e citali nelle "notes"
   con il link. Se non ne riporta, NON scriverlo nelle notes e non rimandare l'utente a cercare: usa la strumentazione
   originale e la tua competenza. Mai risorse a pagamento.
7. Assegna ai footswitch i blocchi che il chitarrista vorra' attivare a mano (es. drive extra, delay).
8. Scrivi le note per il chitarrista in italiano.
9. Se la chitarra ha un pickup piezo e il brano (o la patch) ha parti acustiche, crea uno snapshot "Acustico" pensato per
   il piezo: niente ampli distorto (spegni gli ampli o usa un ampli pulito a gain molto basso), compressore, EQ che toglie
   le basse sotto 100 Hz e ammorbidisce le alte, riverbero; nelle notes scrivi di passare al piezo in quello snapshot.
   Una chitarra con piezo + humbucker copre cosi' sia i pezzi acustici sia quelli distorti senza cambiarla.
"""

IRIDIUM_RULES = """
STRYMON IRIDIUM: il chitarrista usa uno Strymon Iridium nel loop effetti dello Stadium per ampli e cab della linea 1.
Progetta comunque la catena completa (con ampli e cab interni: servono per il Quad Cortex e come riserva), ma aggiungi
il campo "iridium": {"enabled": true, "snapshots": [{"snapshot": "nome snapshot", "amp": "Round|Chime|Punch",
"cab": "A|B|C", "drive": 0-10, "bass": 0-10, "middle": 0-10, "treble": 0-10, "level": 0-10, "room": 0-10, "notes": ""}]}
con UNA voce per ogni snapshot, nello stesso ordine. Ampli dell'Iridium: Round = Fender Deluxe Reverb (puliti, pop),
Chime = Vox AC30 (jangle, crunch brillante), Punch = Marshall Plexi (crunch e distorto rock). Cab A/B/C sono i tre cab
abbinati a ciascun ampli. Per i suoni molto saturi tieni drive alto e usa i drive della linea 1 come boost; level
pareggiato tra snapshot (5 = riferimento).
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
        + RECIPE_FORMAT + KNOB_RULES + GAIN_RULES + RULES + _library_rule() + "\n" + _catalog_block()
    )


def research_prompt(req: DesignRequest) -> str:
    song = " - ".join(x for x in [req.artist, req.title] if x) or req.description
    q = " ".join(x for x in [req.artist, req.title] if x) or req.description
    return (
        f"Sto ricreando su multieffetto il suono di chitarra di: {song}.\n"
        f"Descrizione: {req.description}\n\n"
        "Fai PIU' ricerche web, con angolazioni diverse, prima di rispondere. Per esempio:\n"
        f"- \"{q} guitar tone\", \"{q} guitar rig\", \"{q} amp settings\", intervista o rig rundown del chitarrista;\n"
        f"- \"{q} helix preset\", \"{q} helix patch free\", \"{q} customtone\", \"{q} hx stomp preset\";\n"
        f"- \"{q} quad cortex preset\", \"{q} cortex cloud\";\n"
        f"- video YouTube \"{q} tone helix\" o \"how to sound like\" con le impostazioni mostrate;\n"
        "- forum (Line 6 community, The Gear Page, Reddit r/Line6Helix e r/QuadCortex) per i commenti degli utenti.\n\n"
        "Poi riassumi in italiano, in modo concreto:\n"
        "1. Strumentazione originale (chitarra e pickup, ampli, pedali, effetti) e come cambia il suono tra le parti del brano.\n"
        "2. Impostazioni concrete trovate (gain, EQ, tempi di delay, tipo di riverbero, accordatura), con la fonte.\n"
        "3. Preset GRATUITI trovati per Helix/HX o Quad Cortex: nome, link e cosa ne dicono gli utenti. Se non ne trovi, "
        "scrivi solo \"nessun preset gratuito trovato\".\n"
        "4. IR GRATUITI adatti, con link e giudizi.\n"
        "Ignora tutto cio' che e' a pagamento."
    )


def ir_block(irs: list[str]) -> str:
    if not irs:
        return ""
    shown = irs[:200]
    return ("IR DELL'UTENTE DISPONIBILI (file .wav nelle sue cartelle). Usa gli IR quando il chitarrista li chiede o quando "
            "un IR adatto migliora il suono: metti nel campo \"ir\" di un blocco role \"ir\" (o \"cab\") ESATTAMENTE uno di "
            "questi nomi, scegliendo quello piu' adatto al cono/cassa dell'ampli (es. 4x12 Greenback per Marshall, 2x12 Blue "
            "per Vox). Se usi un blocco \"ir\" togli il cab che sostituisce, uno per ogni ampli:\n"
            + "\n".join(f"- {n}" for n in shown) + (f"\n(e altri {len(irs) - len(shown)})" if len(irs) > len(shown) else ""))


def match_irs(recipe: ToneRecipe, irs: list[str]) -> list[str]:
    """Riporta i nomi IR scelti dall'AI a file esistenti; avvisa per quelli che non trova."""
    warnings = []
    if not irs:
        return warnings
    lower = {n.lower(): n for n in irs}
    stems = {n.rsplit(".", 1)[0].lower(): n for n in irs}
    for b in recipe.chain:
        if not b.ir:
            continue
        key = b.ir.strip().split("/")[-1].split("\\")[-1].lower()
        hit = lower.get(key) or stems.get(key.rsplit(".", 1)[0])
        if not hit:
            words = set(re.findall(r"[a-z0-9]+", key))
            best = max(irs, key=lambda n: len(words & set(re.findall(r"[a-z0-9]+", n.lower()))))
            if len(words & set(re.findall(r"[a-z0-9]+", best.lower()))) >= 2:
                hit = best
        if hit:
            b.ir = hit
        else:
            warnings.append(f"IR \"{b.ir}\" non trovato nelle tue cartelle IR: il blocco {b.label or b.id} resta senza IR")
            b.ir = None
    return warnings


def _user_prompt(req: DesignRequest, research: str) -> str:
    parts = [
        f"CANZONE: {req.title or '-'} | ARTISTA: {req.artist or '-'}",
        f"SUONO RICHIESTO: {req.description or '-'}",
        f"CHITARRA DELL'UTENTE: {req.guitar or 'non specificata'}",
        f"SNAPSHOT RICHIESTI: {req.snapshots or 'decidi tu in base alla canzone'}",
    ]
    if req.gear_preferences:
        parts.append(f"PREFERENZE: {req.gear_preferences}")
    if req.use_iridium:
        parts.append(IRIDIUM_RULES)
    if req.gear:
        parts.append("STRUMENTAZIONE DELL'UTENTE (tarare gain ed EQ su questi pickup; se la chitarra non e' indicata "
                     "scegli la piu' adatta da qui e scrivila in guitar.description; nelle notes indica il pickup da usare "
                     f"in ogni snapshot):\n{req.gear}")
    if req.audio_analysis:
        parts.append(f"ANALISI DELLA TRACCIA AUDIO:\n{req.audio_analysis}")
    if research:
        parts.append(f"RISULTATI DELLA RICERCA WEB:\n{research}")
    if req.irs:
        parts.append(ir_block(req.irs))
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


def apply_gear_rules(recipe: ToneRecipe, iridium: bool | None = None) -> list[str]:
    """Applica i vincoli della strumentazione che l'AI potrebbe non aver rispettato."""
    from .gear import load_gear

    g = load_gear()
    notes: list[str] = []
    if g.wah_external:
        wahs = [b.id for b in recipe.chain if b.role == Role.wah]
        if wahs:
            recipe.chain = [b for b in recipe.chain if b.id not in wahs]
            recipe.footswitches = [f for f in recipe.footswitches if f.block_id not in wahs]
            for s in recipe.snapshots:
                for bid in wahs:
                    s.enabled.pop(bid, None)
                    s.overrides.pop(bid, None)
            notes.append("Tolto il blocco wah: usi il wah esterno.")
    if recipe.iridium is not None:
        recipe.iridium.midi_channel = g.iridium_midi_channel
        recipe.iridium.first_preset = g.iridium_first_preset
        if iridium is False or not g.iridium:
            recipe.iridium.enabled = False
    return notes


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
    warnings += apply_gear_rules(recipe, req.use_iridium)
    warnings += match_irs(recipe, req.irs)
    warnings += check_models(recipe)
    return DesignResult(recipe, research_text, sources, warnings)


EDIT_RULES = """
Ricevi una ricetta esistente e una richiesta di modifica in linguaggio naturale.
Applica SOLO la modifica richiesta, lasciando invariato il resto, e restituisci la ricetta completa.
Esempi: "piu' gain nell'assolo" -> overrides dello snapshot assolo; "metti il delay sul FS3" -> footswitches;
"il mix del riverbero cambia per snapshot" -> overrides del riverbero in ogni snapshot;
"togli il compressore" -> rimuovi il blocco e ogni riferimento negli snapshot/footswitch.
Aggiungi in "notes" UNA riga che inizia con "Modifica:" e spiega cosa hai cambiato e perche'. Se la richiesta e' un
dubbio o una critica (es. "e' troppo scarno"), intervieni sul preset per risolverlo e spiegalo in quella riga.
Non aggiungere altre note, e togli dalle notes le righe che rimandano l'utente a cercare preset o IR da solo.
"""

_RESEARCH_WORDS = ("cerca", "ricerca", "trova", "preset", " ir", "impulse", "customtone", "cortex cloud", "online", "web")


def edit(recipe: ToneRecipe, instruction: str, provider: LLMProvider, *, web_research: bool = True,
         irs: list[str] | None = None) -> DesignResult:
    system = system_prompt() + "\n" + EDIT_RULES
    research_text, sources = "", []
    low = f" {instruction.lower()}"
    if web_research and provider.supports_web and any(w in low for w in _RESEARCH_WORDS):
        req = DesignRequest(title=recipe.song.title, artist=recipe.song.artist,
                            description=f"{recipe.description}. Richiesta: {instruction}")
        try:
            rr = provider.research(research_prompt(req))
            research_text, sources = rr.text, rr.sources
        except AIError:
            pass
    if recipe.iridium and recipe.iridium.enabled:
        system += IRIDIUM_RULES
    irs = irs or []
    wants_ir = bool(re.search(r"\bir\b|impulse|risposta all'impulso", instruction, re.I))
    user = ("RICETTA ATTUALE:\n" + recipe.model_dump_json(indent=1)
            + "\n\nMODIFICA RICHIESTA: " + instruction
            + (f"\n\n{ir_block(irs)}" if irs else "")
            + (f"\n\nRISULTATI DELLA RICERCA WEB:\n{research_text}" if research_text else "")
            + "\n\nRestituisci la ricetta JSON completa.")
    data = provider.complete_json(system, user)
    new = _validate(data, provider, system, user)
    apply_gear_rules(new, recipe.iridium.enabled if recipe.iridium else None)
    ir_warn = match_irs(new, irs)
    added = [n for n in new.notes if n not in recipe.notes]
    changes = [n for n in added if n.lower().startswith("modifica:")]
    # le righe "Modifica:" sono la risposta in chat, non note permanenti del preset
    new.notes = [n for n in new.notes if n not in changes]
    res = DesignResult(new, research=research_text, sources=sources, warnings=ir_warn + check_models(new))
    res.reply = " ".join(c.split(":", 1)[1].strip() for c in changes) or (added[-1] if added else "Modifica applicata.")
    res.changes = diff_recipes(recipe, new)
    if wants_ir and not irs:
        res.reply += (" Attenzione: non ho trovato tuoi file IR. Indica la cartella degli IR in Impostazioni "
                      "(Cartelle IR) e poi chiedimelo di nuovo.")
    elif ir_warn:
        res.reply += " " + " ".join(ir_warn)
    if not res.changes["items"]:
        res.reply = "Non ho cambiato niente nel preset: prova a dirmi quale blocco o snapshot toccare e di quanto."
    return res


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


SUGGEST_PROMPT = """Sei il tecnico di palco di un chitarrista. Per ogni brano della scaletta scegli, tra le chitarre
dell'utente, quella con cui suonarlo, e indica pickup per le parti principali e accordatura.
Criteri, in ordine:
1. rispetta il campo "da usare per" di ogni chitarra: e' la scelta del chitarrista (es. una chitarra per pop e puliti,
   un'altra per acustici e pezzi duri). Non proporre mai le chitarre DI RISERVA;
2. CONTINUITA': tieni la stessa chitarra su brani consecutivi e cambiala solo se il brano lo richiede davvero
   (parte acustica che serve il piezo, distorsione che i single coil non reggono, accordatura diversa). Non serve
   cambiare chitarra solo perche' nel disco ne usavano un'altra: si adatta il suono con la patch;
3. resa del suono originale (single coil, humbucker, P90, piezo).
Nel campo "reason" spiega in breve, e se cambi chitarra rispetto al brano prima di' perche' e' necessario.

Rispondi in JSON: {"songs": [{"index": 0, "guitar": "nome esatto della chitarra", "pickups": "es. ponte per la
strofa, manico per l'assolo", "tuning": "es. standard, mezzo tono sotto", "original": "chitarra usata nel disco",
"reason": "una frase"}]}. Scrivi in italiano."""


def suggest_guitars(songs: list[dict], gear_text: str, provider: LLMProvider) -> list[dict]:
    if not gear_text:
        raise AIError("Prima inserisci la tua strumentazione (pulsante Strumentazione in alto)")
    listing = "\n".join(f"{i}. {s.get('artist') or '?'} - {s.get('title') or '?'}" for i, s in enumerate(songs))
    data = provider.complete_json(SUGGEST_PROMPT, f"CHITARRE DISPONIBILI:\n{gear_text}\n\nSCALETTA:\n{listing}")
    out = []
    for row in data.get("songs", []):
        try:
            i = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(songs):
            out.append({"index": i, **{k: str(row.get(k) or "") for k in ("guitar", "pickups", "tuning", "original", "reason")}})
    return out


__all__ = ["suggest_guitars", "DesignRequest", "DesignResult", "design", "edit", "describe_audio", "system_prompt", "Role"]
