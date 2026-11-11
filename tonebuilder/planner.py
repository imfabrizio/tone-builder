"""Pianificazione della scaletta: patch generiche e dialogo con l'utente.

- `propose_groups`: raggruppa i brani in poche patch generiche corpose.
- `generic_request`: la richiesta di progettazione per una patch generica.
- `converse`: il dialogo (anche a voce). L'AI fa domande e riassume il piano;
  restituisce un'azione da eseguire SOLO quando l'utente da' l'ok esplicito.
"""
from __future__ import annotations

import re
from typing import Any

from .ai.base import AIError, LLMProvider
from .designer import DesignRequest

GENERIC_SNAPSHOTS = "Pulito, Pulito FX (chorus/delay), Crunch, Ritmica, Ritmica piena, Lead, Lead + delay, Boost"

GENERIC_RULES = f"""
QUESTA E' UNA PATCH GENERICA che deve coprire da sola tutti i brani elencati: deve essere CORPOSA e completa,
non un compromesso povero. Regole:
- usa entrambe le linee: linea 1 con gate, compressore, almeno un boost/overdrive, uno o due ampli (es. uno pulito
  e uno saturo, max 2), cab; linea 2 con EQ, almeno una modulazione (chorus o simile), delay e riverbero;
- 12-16 blocchi in totale;
- 6-8 snapshot che coprono tutte le esigenze dei brani, di solito: {GENERIC_SNAPSHOTS}; usa i nomi indicati;
- footswitch per boost, drive extra, modulazione e delay, cosi' si interviene a mano dal vivo;
- volumi pareggiati tra snapshot (level_db), lead +2/+3 dB;
- nelle notes scrivi per OGNI brano quale snapshot usare in strofa, ritornello e assolo.
"""

GROUP_PROMPT = """Sei il tecnico di palco di un chitarrista. Devi ridurre la scaletta a poche PATCH GENERICHE corpose.
Procedi in due passi:
1. CHITARRA: se l'utente ha indicato le sue chitarre, assegna a ogni brano la chitarra con cui suonarlo, rispettando il
   campo "da usare per" di ogni chitarra (e' la sua scelta) e non proponendo mai le chitarre di riserva. Cerca la
   continuita': non far cambiare chitarra senza una necessita' vera (parti acustiche che servono il piezo, distorsioni
   che i single coil non reggono, accordature diverse). Una chitarra con piezo e humbucker copre sia acustici sia
   distorti.
2. PATCH: raggruppa i brani per suono SOLO tra brani con la stessa chitarra: ogni patch usa UNA sola chitarra (non si
   cambia chitarra dentro una patch, perche' volumi e pickup cambiano tutto). Ogni patch, ricca di snapshot, deve coprire
   bene tutti i suoi brani.
Rispetta il numero massimo di patch indicato.

Rispondi in JSON:
{"patches": [{"name": "nome breve (max 24 caratteri)", "description": "il suono della patch e cosa deve coprire",
  "snapshots": "elenco dei nomi degli snapshot separati da virgola",
  "guitar": "nome esatto della chitarra dell'utente usata in tutta la patch",
  "songs": [{"index": 0, "usage": "es. strofa Crunch, ritornello Ritmica piena, assolo Lead"}]}],
 "notes": "una frase su come hai ragionato (chitarre e gruppi)"}
Ogni brano deve stare in una sola patch. Scrivi in italiano."""


def _listing(songs: list[dict]) -> str:
    rows = []
    for i, s in enumerate(songs):
        if s.get("kind") == "patch":
            continue
        extra = ""
        a = (s.get("audio") or {}).get("analysis") or ""
        if a:
            extra = " | ascolto: " + re.sub(r"\s+", " ", a)[:400]
        g = (s.get("request") or {}).get("guitar")
        if g:
            extra += f" | chitarra: {g}"
        rows.append(f"{i}. {s.get('artist') or '?'} - {s.get('title') or '?'}{extra}")
    return "\n".join(rows)


def propose_groups(songs: list[dict], max_patches: int, notes: str, gear_text: str,
                   provider: LLMProvider) -> dict[str, Any]:
    user = (f"NUMERO MASSIMO DI PATCH: {max_patches}\n"
            + (f"INDICAZIONI DEL CHITARRISTA: {notes}\n" if notes else "")
            + (f"STRUMENTAZIONE:\n{gear_text}\n" if gear_text else "")
            + f"\nSCALETTA:\n{_listing(songs)}")
    data = provider.complete_json(GROUP_PROMPT, user)
    return _clean_proposal(data, songs, max_patches)


REVISE_PROMPT = """Sei il tecnico di palco di un chitarrista e state decidendo insieme come ridurre la scaletta a
poche PATCH GENERICHE corpose. Ricevi la proposta attuale e la conversazione. Rispondi in italiano con frasi brevi e
naturali (verranno anche lette ad alta voce), massimo 4 frasi.

- Se il chitarrista chiede cambiamenti (spostare brani, unire o dividere patch, cambiare nome, snapshot, chitarra,
  carattere del suono), aggiorna la proposta e spiega in breve cosa hai cambiato.
- Se fa una domanda, rispondi e lascia la proposta com'e'.
- Se da' un ok esplicito per creare le patch ("ok", "vai", "procedi", "creale"), metti "go": true.
Mantieni sempre ogni brano in una sola patch e rispetta il numero massimo di patch, salvo che lui chieda di cambiarlo.
Ogni patch usa una sola chitarra; segui il criterio del chitarrista su quale chitarra usare per cosa e la continuita'.
Le patch restano corpose: due linee piene, 12-16 blocchi, 6-8 snapshot.

Rispondi SOLO in JSON: {"reply": "...", "go": false, "proposal": {"patches": [...stesso formato...], "notes": "..."}}"""


def _clean_proposal(data: dict, songs: list[dict], max_patches: int) -> dict:
    valid = {i for i, s in enumerate(songs) if s.get("kind") != "patch"}
    seen: set[int] = set()
    patches = []
    for p in (data.get("patches") or [])[:max_patches]:
        rows = []
        for r in p.get("songs", []):
            try:
                i = int(r.get("index"))
            except (TypeError, ValueError):
                continue
            if i in valid and i not in seen:
                seen.add(i)
                rows.append({"index": i, "usage": str(r.get("usage") or "")})
        if rows:
            patches.append({"name": str(p.get("name") or "Patch")[:24], "description": str(p.get("description") or ""),
                            "snapshots": str(p.get("snapshots") or GENERIC_SNAPSHOTS),
                            "guitar": str(p.get("guitar") or ""), "songs": rows})
    missing = sorted(valid - seen)
    if missing and patches:  # brani dimenticati: nella patch piu' numerosa
        patches.sort(key=lambda p: -len(p["songs"]))
        patches[0]["songs"] += [{"index": i, "usage": ""} for i in missing]
    if not patches:
        raise AIError("L'AI non ha proposto patch valide: riprova")
    return {"patches": patches, "notes": str(data.get("notes") or "")}


def revise_groups(songs: list[dict], proposal: dict, history: list[dict], max_patches: int,
                  gear_text: str, provider: LLMProvider) -> dict[str, Any]:
    import json

    convo = "\n".join(f"{'CHITARRISTA' if m.get('who') == 'me' else 'TU'}: {m.get('text', '')}" for m in history[-16:])
    user = (f"NUMERO MASSIMO DI PATCH: {max_patches}\n"
            + (f"STRUMENTAZIONE:\n{gear_text}\n" if gear_text else "")
            + f"\nSCALETTA:\n{_listing(songs)}\n\nPROPOSTA ATTUALE:\n{json.dumps(proposal, ensure_ascii=False)}"
            + f"\n\nCONVERSAZIONE:\n{convo}\n\nRispondi all'ultimo messaggio.")
    data = provider.complete_json(REVISE_PROMPT, user)
    new = data.get("proposal") if isinstance(data.get("proposal"), dict) else proposal
    # se il chitarrista ha chiesto piu' patch, il nuovo massimo e' quello proposto
    limit = max(max_patches, len(new.get("patches") or [])) if len(new.get("patches") or []) <= 8 else 8
    cleaned = _clean_proposal(new, songs, limit)
    last = next((m.get("text", "") for m in reversed(history) if m.get("who") == "me"), "")
    go = bool(data.get("go")) and bool(_OK.search(last))
    return {"reply": str(data.get("reply") or "Ok."), "proposal": cleaned, "go": go}


def generic_request(group: dict, songs: list[dict], gear_text: str, use_iridium: bool = False) -> DesignRequest:
    names = []
    analyses = []
    for r in group["songs"]:
        s = songs[r["index"]]
        names.append(f"- {s.get('artist', '')} - {s.get('title', '')}" + (f" ({r['usage']})" if r.get("usage") else ""))
        a = (s.get("audio") or {}).get("analysis")
        if a:
            short = re.sub(r"\s+", " ", a)[:600]
            analyses.append(f"{s.get('title', '')}: {short}")
    desc = (f"{group.get('description', '')}\nBRANI DA COPRIRE:\n" + "\n".join(names) + "\n" + GENERIC_RULES)
    return DesignRequest(
        title=group["name"], artist="", description=desc, guitar=group.get("guitar", ""),
        snapshots=group.get("snapshots") or GENERIC_SNAPSHOTS, gear=gear_text,
        audio_analysis="\n".join(analyses)[:6000], use_iridium=use_iridium,
    )


# ---------------------------------------------------------------- dialogo

DIALOG_PROMPT = """Sei il tecnico del suono di un chitarrista e parli con lui (spesso a voce) per progettare i suoni
della sua scaletta su Helix Stadium e Quad Cortex. Rispondi in italiano, frasi brevi e naturali, adatte a essere
lette ad alta voce: niente elenchi lunghi, niente markdown, massimo 4-5 frasi.

Il tuo compito:
1. capire cosa vuole: fai UNA domanda alla volta quando manca qualcosa di importante (suono, sezioni, chitarra, quante
   patch, quali brani);
2. quando hai un piano chiaro, riassumilo in poche frasi e chiedi se puoi procedere, e metti SUBITO l'azione nel campo
   "action": il programma la mostra come proposta con un pulsante di conferma e la esegue solo dopo l'ok del
   chitarrista. Quindi ogni volta che proponi qualcosa di concreto, "action" NON deve essere null;
3. quando l'utente da' l'ok ("ok", "vai", "procedi", "si', fallo") restituisci di nuovo l'azione completa: verra' eseguita.
   Non ripetere il riassunto: conferma in una frase.
4. se ti chiede una modifica a un preset gia' fatto, usa "edit_song" sul brano giusto con un'istruzione precisa
   (cosa cambiare e di quanto). Se il brano usa una patch generica, la modifica va fatta sulla patch.
5. non ripetere mai la stessa risposta del turno prima: se l'utente insiste, vuol dire che non hai capito o che non hai
   proposto l'azione; correggiti.

Azioni possibili (campo "action"):
- {"type": "design_song", "song_index": N, "description": "...", "snapshots": "...", "guitar": "...", "gear_preferences": "..."}
  crea (o rifa' da zero) il preset di un brano;
- {"type": "edit_song", "song_index": N, "instruction": "..."} modifica il preset esistente di un brano;
- {"type": "generic_patches", "max_patches": N, "notes": "indicazioni del chitarrista"} riduce la scaletta a patch generiche
  corpose e le crea;
- {"type": "edit_many", "target": "patches" | "songs" | "all" | [indici], "instruction": "..."} applica la stessa
  modifica a piu' preset ESISTENTI (es. "usa gli IR in tutte le patch", "meno riverbero ovunque"): li modifica, non li
  ricrea;
- {"type": "analyze_youtube"} cerca su YouTube e analizza i brani della scaletta non ancora analizzati;
- {"type": "design_all", "description": "indicazioni comuni", "redo": false} crea i preset dei brani che non ce l'hanno,
  usando la descrizione e l'analisi di ciascun brano ("redo": true solo se chiede di rifarli tutti da zero).

MODIFICARE NON E' RICREARE: se il chitarrista chiede di cambiare qualcosa in preset o patch che esistono gia', usa SEMPRE
"edit_song" (uno) o "edit_many" (piu' di uno). "design_song", "design_all" con redo e "generic_patches" sostituiscono
quello che c'e' e vanno usati solo se chiede esplicitamente di rifare da zero o di creare nuove patch.

Rispondi SOLO in JSON: {"reply": "testo da dire all'utente", "action": null oppure un oggetto azione}.
Quando restituisci un'azione, "reply" conferma in una frase cosa stai per fare.
IMPORTANTE: l'azione parte solo quando il chitarrista conferma. Se "action" e' null NON dire mai che stai iniziando, che stai lavorando o che hai fatto qualcosa: in quel
caso riassumi cosa faresti e chiedi l'ok. Se ti chiede se un lavoro e' partito o a che punto e', rispondi SOLO in base
alla sezione LAVORI IN CORSO del contesto: se li' non c'e', non e' partito niente."""


def setlist_context(setlist: dict, current: int | None, gear_text: str, jobs_text: str = "") -> str:
    rows = []
    for i, s in enumerate(setlist.get("songs", [])):
        state = "patch generica" if s.get("kind") == "patch" else ("preset pronto" if s.get("recipe") else "senza preset")
        if s.get("uses_patch"):
            state = f"usa la patch {s.get('uses_patch_name', '')}"
        heard = ", ascoltato" if (s.get("audio") or {}).get("analysis") else ""
        mark = "  <- brano aperto" if current == i else ""
        rows.append(f"{i}. {s.get('artist') or ''} - {s.get('title') or 'senza titolo'} [{state}{heard}]{mark}")
    out = f"SCALETTA \"{setlist.get('name', '')}\":\n" + ("\n".join(rows) or "(vuota)")
    if current is not None and 0 <= current < len(setlist.get("songs", [])):
        s = setlist["songs"][current]
        if s.get("recipe"):
            r = s["recipe"]
            chain = ", ".join(f"{b.get('label') or b.get('gear_ref') or b['role']} (L{b.get('line', 1)})" for b in r.get("chain", []))
            snaps = ", ".join(x.get("name", "") for x in r.get("snapshots", []))
            out += f"\n\nPRESET DEL BRANO APERTO \"{r.get('name', '')}\": blocchi {chain}; snapshot {snaps}"
    if gear_text:
        out += f"\n\nSTRUMENTAZIONE:\n{gear_text}"
    out += "\n\nLAVORI IN CORSO:\n" + (jobs_text or "nessuno")
    return out


_OK = re.compile(r"\b(ok|okay|va bene|vai|procedi|fallo|falla|falle|si'|sì|si\b|confermo|perfetto|d'accordo|esatto|avanti|crea)", re.I)


ACTION_LABELS = {
    "design_song": "creare il preset del brano",
    "edit_song": "modificare il preset del brano",
    "generic_patches": "creare le patch generiche",
    "analyze_youtube": "ascoltare i brani da YouTube",
    "design_all": "creare i preset di tutti i brani",
    "edit_many": "modificare tutti i preset",
}

_REDO = re.compile(r"\b(rifa\w*|ricre\w*|da zero|da capo|nuov[oaie]|crea\w*|genera\w*)\b", re.I)


def _guard_action(action: dict, setlist: dict | None, last: str) -> tuple[dict, str]:
    """Evita che una richiesta di modifica diventi un preset nuovo. Ritorna (azione, nota per l'utente)."""
    songs = (setlist or {}).get("songs", [])
    t = action.get("type")
    if t == "design_song" and not _REDO.search(last):
        idx = action.get("song_index")
        if isinstance(idx, int) and 0 <= idx < len(songs) and (songs[idx].get("recipe") or songs[idx].get("uses_patch")):
            instr = action.get("instruction") or action.get("description") or last
            return {"type": "edit_song", "song_index": idx, "instruction": instr}, ""
    if t == "generic_patches" and any(x.get("kind") == "patch" for x in songs) and not _REDO.search(last):
        return {"type": "edit_many", "target": "patches", "instruction": action.get("notes") or last}, ""
    if t == "design_all" and action.get("redo") and not _REDO.search(last):
        return {**action, "redo": False}, ""
    return action, ""


def describe_action(action: dict, setlist: dict | None = None) -> str:
    label = ACTION_LABELS.get(action.get("type", ""), "eseguire l'operazione")
    idx = action.get("song_index")
    if setlist and isinstance(idx, int) and 0 <= idx < len(setlist.get("songs", [])):
        label += f" \"{setlist['songs'][idx].get('title') or 'senza titolo'}\""
    if action.get("type") == "generic_patches" and action.get("max_patches"):
        label += f" (massimo {action['max_patches']}, sostituiscono quelle attuali)"
    if action.get("type") == "edit_many":
        tgt = action.get("target")
        label = {"patches": "Modificare tutte le patch generiche", "songs": "Modificare i preset di tutti i brani"}.get(
            tgt, f"Modificare {len(tgt)} preset" if isinstance(tgt, list) else "Modificare tutti i preset")
        if action.get("instruction"):
            label += f": {str(action['instruction'])[:80]}"
    if action.get("type") == "edit_song" and action.get("instruction"):
        label += f": {str(action['instruction'])[:80]}"
    if action.get("type") == "design_all" and action.get("redo"):
        label = "Rifare da zero i preset di tutti i brani"
    return label[0].upper() + label[1:]


def converse(history: list[dict], context: str, provider: LLMProvider, setlist: dict | None = None) -> dict[str, Any]:
    convo = "\n".join(f"{'CHITARRISTA' if m.get('who') == 'me' else 'TU'}: {m.get('text', '')}" for m in history[-20:])
    data = provider.complete_json(DIALOG_PROMPT, f"{context}\n\nCONVERSAZIONE:\n{convo}\n\nRispondi all'ultimo messaggio.")
    reply = str(data.get("reply") or "").strip() or "Puoi ripetere?"
    action = data.get("action") if isinstance(data.get("action"), dict) else None
    if action and action.get("type") not in ACTION_LABELS:
        action = None
    last = next((m.get("text", "") for m in reversed(history) if m.get("who") == "me"), "")
    if action:
        action, _ = _guard_action(action, setlist, " ".join(m.get("text", "") for m in history[-6:] if m.get("who") == "me"))
    pending = None
    if action and not _OK.search(last):
        # niente azioni senza un ok esplicito: l'azione diventa una proposta da confermare con un clic
        pending, action = action, None
        reply = f"{reply}\n\nPer partire confermami con un ok, oppure premi il pulsante."
    out: dict[str, Any] = {"reply": reply, "action": action}
    if pending:
        out["pending_action"] = pending
        out["pending_label"] = describe_action(pending, setlist)
    if action:
        out["action_label"] = describe_action(action, setlist)
    return out


def transcribe_prompt() -> str:
    return ("Trascrivi esattamente cio' che dice la persona in questo audio, in italiano. Restituisci solo il testo, "
            "senza commenti. Se non si sente nulla, restituisci una stringa vuota.")


__all__ = ["revise_groups", "propose_groups", "generic_request", "converse", "setlist_context", "transcribe_prompt", "GENERIC_SNAPSHOTS"]
